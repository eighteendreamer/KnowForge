from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from kombu.exceptions import OperationalError
from sqlalchemy import select

from app.core.errors import AppError
from app.models import Document, IndexRebuild, ProcessingTask, RuntimeConfiguration
from app.services import task_queue
from app.services.pipeline import new_task

# Imported by name so the `context` fixture's monkeypatch of the module attribute leaves these objects intact.
consumer_online = task_queue.consumer_online
dispatch_named = task_queue.dispatch_named


class FakeInspector:
    def __init__(self, reply: bool, raises: bool = False):
        self.reply, self.raises, self.calls = reply, raises, 0

    def ping(self):
        self.calls += 1
        if self.raises:
            raise ConnectionError("broker is down")
        return {"celery@one": ["pong"]} if self.reply else None


class FakeCelery:
    def __init__(self, reply: bool = True, raises: bool = False):
        self.inspector = FakeInspector(reply, raises)
        self.control = SimpleNamespace(inspect=lambda timeout=None: self.inspector)
        self.conf = SimpleNamespace(broker_url="redis://fake-" + uuid4().hex + "/0")
        self.sent: list[tuple[str, list]] = []

    def send_task(self, name, args=None, retry=False):
        self.sent.append((name, list(args or [])))


class RefusingCelery(FakeCelery):
    def send_task(self, name, args=None, retry=False):
        raise OperationalError("redis refused the message")


@pytest.fixture(autouse=True)
def fresh_consumer_cache():
    task_queue._consumers.clear()
    yield
    task_queue._consumers.clear()


async def _task_running_since(context, age_seconds: float) -> None:
    document = Document(
        doc_id="doc_" + uuid4().hex,
        title="probe",
        original_filename="probe.html",
        file_type="html",
        file_size=10,
        source_path="documents/probe.html",
        status="ready",
    )
    async with context["sessions"]() as session:
        session.add(document)
        await session.flush()
        task = new_task(document, context["runtime"])
        task.status = "running"
        task.updated_at = datetime.now(UTC) - timedelta(seconds=age_seconds)
        session.add(task)
        await session.commit()


async def _offline_celery(context) -> FakeCelery:
    celery = FakeCelery(reply=False)
    async with context["sessions"]() as session:
        return celery, await consumer_online(celery, session)


async def test_a_ping_reply_counts_as_a_consumer_even_with_no_work(context):
    celery = FakeCelery(reply=True)
    async with context["sessions"]() as session:
        assert await consumer_online(celery, session) is True


async def test_a_silent_worker_is_offline_while_nothing_is_running(context):
    celery, online = await _offline_celery(context)
    assert online is False
    assert celery.inspector.calls == 1


async def test_a_busy_solo_worker_that_ignores_ping_counts_as_a_consumer_while_its_task_is_fresh(context):
    await _task_running_since(context, age_seconds=5)
    _, online = await _offline_celery(context)
    assert online is True


async def test_a_dead_worker_stops_counting_once_its_running_task_goes_stale(context):
    await _task_running_since(context, age_seconds=task_queue.BUSY_WORKER_FRESHNESS_SECONDS + 180)
    _, online = await _offline_celery(context)
    assert online is False


async def test_a_broker_error_is_never_reported_as_a_consumer(context):
    celery = FakeCelery(raises=True)
    async with context["sessions"]() as session:
        assert await consumer_online(celery, session) is False


async def test_consumer_state_is_cached_so_repeated_submissions_pay_one_ping(context):
    celery = FakeCelery(reply=True)
    async with context["sessions"]() as session:
        assert await consumer_online(celery, session) is True
        assert await consumer_online(celery, session) is True
    assert celery.inspector.calls == 1


async def test_offline_answers_expire_faster_than_online_ones(context):
    celery = FakeCelery(reply=False)
    async with context["sessions"]() as session:
        assert await consumer_online(celery, session) is False
    _, expires = task_queue._consumers[celery.conf.broker_url]
    assert expires <= task_queue.time.monotonic() + task_queue.CONSUMER_NEGATIVE_TTL_SECONDS


async def test_a_running_rebuild_also_proves_a_consumer_exists(context):
    """Rebuilds have no Beat recovery, so their progress must open the gate too."""
    async with context["sessions"]() as session:
        configuration = await session.scalar(select(RuntimeConfiguration).limit(1))
        session.add(
            IndexRebuild(
                id=uuid4(),
                source_configuration_id=configuration.id,
                target_configuration_id=configuration.id,
                target_collection="knowforge_rebuild_probe_" + uuid4().hex,
                target_values={},
                target_fingerprint="f" * 64,
                source_revision="probe",
                status="running",
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()
    _, online = await _offline_celery(context)
    assert online is True


async def test_broker_failure_surfaces_as_503_instead_of_a_silent_false(context, monkeypatch):
    async def online(celery, session):
        return True

    monkeypatch.setattr(task_queue, "consumer_online", online)
    async with context["sessions"]() as session:
        with pytest.raises(AppError) as raised:
            await dispatch_named(RefusingCelery(), session, "knowforge.process", ["task-1"])
    assert raised.value.status == 503


async def test_dispatch_refuses_to_accept_a_task_no_one_will_run(context, monkeypatch):
    async def offline(celery, session):
        return False

    monkeypatch.setattr(task_queue, "consumer_online", offline)
    celery = FakeCelery()
    async with context["sessions"]() as session:
        with pytest.raises(AppError) as raised:
            await dispatch_named(celery, session, "knowforge.process", ["task-1"])
    assert raised.value.status == 409
    assert celery.sent == []


async def test_processing_task_rows_are_the_only_ingest_proof_needed(context):
    async with context["sessions"]() as session:
        await _task_running_since(context, age_seconds=1)
        celery = FakeCelery(reply=False)
        assert await consumer_online(celery, session) is True
        stale = await session.scalar(select(ProcessingTask))
        stale.updated_at = datetime.now(UTC) - timedelta(days=1)
        await session.commit()
    task_queue._consumers.clear()
    _, online = await _offline_celery(context)
    assert online is False
