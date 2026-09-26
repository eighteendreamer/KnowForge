import re
from collections import Counter
from pathlib import Path

import pymupdf

from app.core.config import Settings
from app.schemas.documents import Element, ParsedContent
from app.services.parser.html_parser import ParseError, markdown_table

NUMBERED_HEADING = re.compile(
    r"^(?:第[一二三四五六七八九十\d]+[章节]|[一二三四五六七八九十]+[、．.]|\d+(?:\.\d+)*[、．.)）]\s*\S)"
)


def parse_pdf(path: Path, settings: Settings) -> ParsedContent:
    try:
        document = pymupdf.open(path)
    except (pymupdf.FileDataError, pymupdf.EmptyFileError) as exc:
        raise ParseError("PDF 已损坏或无法读取") from exc
    with document:
        if document.needs_pass:
            raise ParseError("不支持加密 PDF，请先解密后上传")
        if not 0 < len(document) <= settings.pdf_max_pages:
            raise ParseError("PDF 页数为空或超过限制")
        elements: list[Element] = []
        recognition_pages: list[int] = []
        for page_index in range(len(document)):
            page = document[page_index]
            text = page.get_text().strip()
            images = page.get_images()
            invalid_ratio = text.count("\ufffd") / max(1, len(text))
            if invalid_ratio > 0.05 or (len(text) < 40 and images) or (not text and page.get_drawings()):
                recognition_pages.append(page.number + 1)
                continue
            if not text:
                continue
            blocks = page.get_text("dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)[
                "blocks"
            ]
            sizes: Counter[float] = Counter()
            for block in blocks:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        sizes[round(span["size"], 1)] += len(span["text"].strip())
            body_size = sizes.most_common(1)[0][0] if sizes else 12
            tables = page.find_tables().tables
            ordered: list[tuple[float, float, Element]] = []
            for table in tables:
                rows = [[cell or "" for cell in row] for row in table.extract()]
                table_text = markdown_table(rows)
                if table_text:
                    ordered.append(
                        (
                            table.bbox[1],
                            table.bbox[0],
                            Element(type="Table", text=table_text, page=page.number + 1),
                        )
                    )
            for block in blocks:
                lines: list[str] = []
                line_y = block["bbox"][1]
                for line in block.get("lines", []):
                    center = (
                        pymupdf.Rect(line["bbox"]).tl
                        + (pymupdf.Rect(line["bbox"]).br - pymupdf.Rect(line["bbox"]).tl) * 0.5
                    )
                    if any(center in pymupdf.Rect(table.bbox) for table in tables):
                        continue
                    value = "".join(span["text"] for span in line["spans"]).strip()
                    if not value:
                        continue
                    max_size = max(span["size"] for span in line["spans"])
                    is_heading = len(value) <= 160 and (
                        max_size >= body_size * 1.2 or NUMBERED_HEADING.match(value)
                    )
                    if is_heading:
                        if lines:
                            ordered.append(
                                (
                                    line_y,
                                    block["bbox"][0],
                                    Element(
                                        type="NarrativeText", text="\n".join(lines), page=page.number + 1
                                    ),
                                )
                            )
                            lines = []
                        level = 1 if max_size >= body_size * 1.5 else 2
                        ordered.append(
                            (
                                line["bbox"][1],
                                line["bbox"][0],
                                Element(type="Heading", level=level, text=value, page=page.number + 1),
                            )
                        )
                    else:
                        if not lines:
                            line_y = line["bbox"][1]
                        lines.append(value)
                if lines:
                    ordered.append(
                        (
                            line_y,
                            block["bbox"][0],
                            Element(type="NarrativeText", text="\n".join(lines), page=page.number + 1),
                        )
                    )
            elements.extend(item[2] for item in sorted(ordered, key=lambda item: (item[0], item[1])))
        if not elements and not recognition_pages:
            raise ParseError("PDF 未提取到正文")
        return ParsedContent(
            title=((document.metadata or {}).get("title") or path.stem)[:500],
            elements=elements,
            total_pages=len(document),
            recognition_pages=recognition_pages,
        )


def render_page(path: Path, page_number: int, settings: Settings) -> bytes:
    with pymupdf.open(path) as document:
        page = document[page_number - 1]
        scale = min(2.0, (settings.pdf_max_pixels / (page.rect.width * page.rect.height)) ** 0.5)
        width = max(1, int(page.rect.width * scale))
        height = max(1, min(int(page.rect.height * scale), settings.pdf_max_pixels // width))
        scale = min(width / page.rect.width, height / page.rect.height)
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        if pixmap.width * pixmap.height > settings.pdf_max_pixels:
            raise ParseError("页面渲染尺寸超过限制")
        return pixmap.tobytes("png")
