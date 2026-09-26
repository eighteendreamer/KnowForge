import os
import time
from datetime import UTC, datetime
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest, multiprocess
from prometheus_client.core import GaugeMetricFamily
from sqlalchemy import func, select

from app.models import IndexRebuild, ProcessingTask
from app.services.runtime_config import active_settings
from app.services.task_queue import consumer_online

REGISTRY = CollectorRegistry()
HTTP_REQUESTS = Counter(
    "knowforge_http_requests_total",
    "HTTP responses by route template",
    ["method", "route", "status"],
    registry=REGISTRY,
)
HTTP_DURATION = Histogram(
    "knowforge_http_request_duration_seconds",
    "Time until response headers (SSE stream lifetime excluded)",
    ["method", "route"],
    buckets=(0.01, 0.05, 0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
    registry=REGISTRY,
)
MODEL_REQUESTS = Counter(
    "knowforge_model_requests_total",
    "Remote model attempts, including retries",
    ["operation", "model", "priority", "outcome"],
    registry=REGISTRY,
)
MODEL_DURATION = Histogram(
    "knowforge_model_request_duration_seconds",
    "Remote model attempt duration, excluding quota wait",
    ["operation", "model", "priority"],
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 120),
    registry=REGISTRY,
)
MODEL_WAIT = Histogram(
    "knowforge_model_quota_wait_seconds",
    "Time waiting for shared concurrency and rate quota",
    ["operation", "priority", "outcome"],
    buckets=(0.01, 0.05, 0.1, 0.5, 1, 5, 10, 30, 60, 120),
    registry=REGISTRY,
)
MODEL_RETRIES = Counter(
    "knowforge_model_retries_total",
    "Scheduled model retries",
    ["operation", "model", "reason"],
    registry=REGISTRY,
)
MODEL_TOKENS = Counter(
    "knowforge_model_tokens_total",
    "Token usage reported by provider; total includes input and output",
    ["operation", "model", "kind"],
    registry=REGISTRY,
)
SEARCH_CACHE = Counter(
    "knowforge_search_cache_total", "Search cache outcomes", ["mode", "scope", "outcome"], registry=REGISTRY
)
TASK_RUNS = Counter(
    "knowforge_task_runs_total", "Finished document task attempts", ["type", "outcome"], registry=REGISTRY
)
TASK_DURATION = Histogram(
    "knowforge_task_duration_seconds",
    "Document task attempt duration",
    ["type", "outcome"],
    buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1800, 3600),
    registry=REGISTRY,
)


def record_usage(response: Any, operation: str, model: str) -> None:
    usage = response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
    if usage is None:
        return
    for field, kind in [
        ("prompt_tokens", "input"),
        ("completion_tokens", "output"),
        ("total_tokens", "total"),
    ]:
        value = usage.get(field) if isinstance(usage, dict) else getattr(usage, field, None)
        if type(value) is int and value >= 0:
            MODEL_TOKENS.labels(operation, model, kind).inc(value)


class StateCollector:
    def __init__(
        self,
        tasks: dict[str, int],
        oldest: float,
        leases: int,
        waiters: int,
        capacity: int,
        consumers: int,
        rebuild_age: float,
    ):
        self.tasks, self.oldest = tasks, oldest
        self.leases, self.waiters, self.capacity = leases, waiters, capacity
        self.consumers, self.rebuild_age = consumers, rebuild_age

    def collect(self):
        tasks = GaugeMetricFamily("knowforge_tasks", "Persisted task counts", labels=["status"])
        for status in ("pending", "running", "succeeded", "failed"):
            tasks.add_metric([status], self.tasks.get(status, 0))
        yield tasks
        for name, help_text, value in [
            ("knowforge_oldest_pending_task_seconds", "Age of oldest pending task", self.oldest),
            ("knowforge_model_active_leases", "Unexpired shared model request leases", self.leases),
            ("knowforge_model_online_waiters", "Unexpired online requests waiting for quota", self.waiters),
            ("knowforge_model_concurrency_limit", "Configured model concurrency limit", self.capacity),
            (
                "knowforge_queue_consumer_online",
                "A Celery worker answers control or is actively working",
                self.consumers,
            ),
            (
                "knowforge_oldest_pending_rebuild_seconds",
                "Age of the oldest pending index rebuild",
                self.rebuild_age,
            ),
        ]:
            yield GaugeMetricFamily(name, help_text, value=value)


async def render_metrics(app) -> bytes:
    def age(value: datetime | None) -> float:
        return max(0, (datetime.now(UTC) - value).total_seconds()) if value else 0

    async with app.state.sessions() as session:
        counts = dict(
            (
                await session.execute(
                    select(ProcessingTask.status, func.count()).group_by(ProcessingTask.status)
                )
            ).all()
        )
        oldest = await session.scalar(
            select(func.min(ProcessingTask.created_at)).where(ProcessingTask.status == "pending")
        )
        pending_rebuild = await session.scalar(
            select(func.min(IndexRebuild.created_at)).where(IndexRebuild.status == "pending")
        )
        consumers = int(await consumer_online(app.state.celery, session))
        capacity = (await active_settings(session, app.state.settings)).model_max_concurrency
    prefix = app.state.models.prefix
    async with app.state.redis.pipeline(transaction=False) as pipe:
        pipe.zcount(prefix + "leases", time.time(), "+inf")
        pipe.zcount(prefix + "online_waiters", time.time(), "+inf")
        leases, waiters = await pipe.execute()
    snapshot = CollectorRegistry()
    snapshot.register(
        StateCollector(
            counts,
            age(oldest),
            leases,
            waiters,
            capacity,
            consumers,
            age(pending_rebuild),
        )
    )
    registry = REGISTRY
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
    return generate_latest(registry) + generate_latest(snapshot)
