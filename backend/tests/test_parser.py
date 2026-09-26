import pymupdf
import pytest

from app.core.config import ROOT, Settings
from app.schemas.documents import Element
from app.services.parser.chunker import Chunker
from app.services.parser.html_parser import ParseError, parse_html
from app.services.parser.pdf_parser import parse_pdf, render_page


@pytest.fixture
def chunker():
    settings = Settings()
    return Chunker(settings.tokenizer_path)


def test_html_structure_table_and_noise(tmp_path):
    path = tmp_path / "sample.html"
    path.write_text(
        """<!doctype html><html><head><title>缓存知识</title></head><body>
    <nav>导航</nav><main><h1>Redis</h1><h2>缓存穿透</h2><p>使用<b>布隆过滤器</b>防止无效请求。</p>
    <table><tr><th>方案</th><th>优点</th></tr><tr><td>空值缓存</td><td>简单</td></tr></table>
    <pre>GET cache:key</pre><script>steal()</script></main></body></html>""",
        encoding="utf-8",
    )
    result = parse_html(path)
    assert result.title == "缓存知识"
    assert [element.type for element in result.elements] == [
        "Heading",
        "Heading",
        "NarrativeText",
        "Table",
        "Code",
    ]
    assert result.elements[1].level == 2
    assert "| 方案 | 优点 |" in result.elements[3].text
    assert all("导航" not in item.text and "steal" not in item.text for item in result.elements)
    assert result.total_pages is None


def test_text_pdf_heading_and_source_page(tmp_path):
    path = tmp_path / "text.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 50), "Distributed Transactions", fontsize=24)
        page.insert_text(
            (50, 90),
            "Two phase commit coordinates multiple participants to keep data consistent.",
            fontsize=11,
        )
        document.save(path)
    result = parse_pdf(path, Settings())
    assert result.total_pages == 1
    assert result.recognition_pages == []
    assert result.elements[0].type == "Heading"
    assert all(element.page == 1 for element in result.elements)
    assert "Two phase commit" in result.elements[-1].text


def test_scanned_pdf_requires_recognition(tmp_path):
    path = tmp_path / "scan.pdf"
    with pymupdf.open() as source:
        page = source.new_page()
        page.insert_text((50, 50), "Scanned technical knowledge about message queues.", fontsize=14)
        image = page.get_pixmap().tobytes("png")
    with pymupdf.open() as scanned:
        page = scanned.new_page()
        page.insert_image(page.rect, stream=image)
        scanned.save(path)
    result = parse_pdf(path, Settings())
    assert result.recognition_pages == [1]
    assert not result.elements
    assert render_page(path, 1, Settings()).startswith(b"\x89PNG")


def test_encrypted_and_invalid_pdf_rejected(tmp_path):
    encrypted = tmp_path / "encrypted.pdf"
    with pymupdf.open() as doc:
        doc.new_page()
        doc.save(encrypted, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="secret")
    with pytest.raises(ParseError, match="加密"):
        parse_pdf(encrypted, Settings())
    invalid = tmp_path / "broken.pdf"
    invalid.write_bytes(b"not a pdf")
    with pytest.raises(ParseError, match="损坏"):
        parse_pdf(invalid, Settings())


def test_structure_chunks_keep_context_and_whole_tables(chunker):
    table = "| 名称 | 说明 |\n|---|---|\n| 2PC | 两阶段提交 |"
    elements = [
        Element(type="Heading", text="分布式事务", level=1, page=1),
        Element(type="Heading", text="2PC", level=2, page=1),
        Element(type="NarrativeText", text="协调者负责事务提交与回滚。", page=1),
        Element(type="Table", text=table, page=2),
    ]
    result = chunker.build("doc_test", elements, "transactions.pdf")
    assert len(result) == 2
    assert result[0].section_path == ["分布式事务", "2PC"]
    assert result[0].text_with_context.startswith("[分布式事务 > 2PC]")
    assert result[1].text == table
    assert result[1].page_start == 2
    assert result[1].metadata["table_summary"] == table
    assert result == chunker.build("doc_test", elements, "transactions.pdf")


def test_recursive_token_chunks_never_drop_text(chunker):
    text = "缓存穿透需要布隆过滤器与空值缓存，缓存击穿使用互斥锁。" * 200
    chunks = chunker.build("doc_long", [Element(type="NarrativeText", text=text, page=3)], "long.pdf")
    assert len(chunks) > 1
    assert all(chunk.token_count <= 600 for chunk in chunks)
    assert chunks[0].text.startswith(text[:20])
    assert chunks[-1].text.endswith(text[-20:])
    assert all(chunk.page_start == chunk.page_end == 3 for chunk in chunks)
    assert sum(chunk.char_count for chunk in chunks) >= len(text)


def test_overlong_table_fails_without_truncation(chunker):
    table = "| 缓存穿透 | 布隆过滤器 |\n" * 3000
    with pytest.raises(ParseError, match="超过模型"):
        chunker.build("doc_table", [Element(type="Table", text=table)], "table.html")


def test_ten_real_user_pdfs_parse_and_chunk(chunker):
    files = sorted((ROOT / "docs-spider/pdfs").rglob("*.pdf"), key=lambda path: path.stat().st_size)
    assert len(files) >= 10
    for index, path in enumerate(files[:10]):
        parsed = parse_pdf(path, Settings())
        assert parsed.elements, path.name
        chunks = chunker.build(f"doc_real_{index}", parsed.elements, path.name)
        assert chunks, path.name
        assert all(chunk.token_count <= 8192 and chunk.page_start is not None for chunk in chunks)


def test_real_scan_render_stays_within_pixel_budget():
    path = next((ROOT / "docs-spider/pdfs").rglob("web架构师训练营-服务.pdf"))
    image = render_page(path, 1, Settings())
    pixmap = pymupdf.Pixmap(image)
    assert pixmap.width * pixmap.height <= Settings().pdf_max_pixels


def test_real_scanned_documents_are_not_marked_empty():
    files = list((ROOT / "docs-spider/pdfs").rglob("web架构师训练营-*.pdf"))
    assert len(files) == 2
    for path in files:
        result = parse_pdf(path, Settings())
        assert len(result.recognition_pages) == result.total_pages
