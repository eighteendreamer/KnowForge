import pytest
from acceptance_ingest import throughput_report


def test_throughput_report_keeps_model_side_documents_out_of_the_cpu_rate():
    report = throughput_report(
        pages=1000,
        recognition_pages=120,
        chunks=2400,
        documents=31,
        failures=["a.pdf: ValueError: boom"],
        seconds=120,
        scanned_documents=2,
        scanned_pages=40,
    )
    assert report["text_pages"] == 880
    assert report["recognition_pages"] == 120
    assert report["scanned_documents"] == 2 and report["scanned_pages"] == 40
    # 速率只按本机真的解析出来的量算：1000 页 / 2400 块 / (31-2) 篇，各除以 2 分钟。
    assert report["pages_per_minute"] == 500.0
    assert report["chunks_per_minute"] == 1200.0
    assert report["documents_per_minute"] == 14.5
    assert report["failed_documents"] == 1
    assert report["failure_rate"] == round(1 / 31, 4)


def test_throughput_report_refuses_an_empty_corpus():
    with pytest.raises(ValueError):
        throughput_report(0, 0, 0, 0, [], 1.0)
