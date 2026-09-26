from acceptance_load import summarize

K6_SUMMARY = {
    "metrics": {
        "http_req_duration{scenario:cached}": {
            "type": "trend",
            "contains": "time",
            "values": {"min": 32.0, "med": 35.0, "max": 55.0, "p(95)": 40.0, "p(99)": 44.0, "avg": 35.7},
        },
        "http_reqs{scenario:cached}": {"type": "counter", "values": {"count": 90, "rate": 14.99}},
        "http_req_failed{scenario:cached}": {
            "type": "rate",
            "values": {"rate": 0.0, "passes": 0, "fails": 90},
        },
        "knowforge_errors{scenario:cached}": {"type": "counter", "values": {"count": 0, "rate": 0.0}},
    }
}


def test_summarize_reads_the_values_block_that_k6_nests_metrics_under():
    stats = summarize(K6_SUMMARY, "cached")
    assert stats["requests"] == 90
    assert stats["p50_ms"] == 35.0
    assert stats["p95_ms"] == 40.0
    assert stats["p99_ms"] == 44.0
    assert stats["failed_rate"] == 0.0
    assert stats["application_errors"] == 0


def test_summarize_reports_nothing_when_the_scenario_ran_zero_iterations():
    stats = summarize({"metrics": {}}, "cached")
    assert stats["p95_ms"] is None
    assert stats["requests"] is None
