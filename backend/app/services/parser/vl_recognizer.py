import re
from pathlib import Path

from app.core.config import Settings
from app.core.model_client import ModelGateway
from app.schemas.documents import Element, ParsedContent
from app.services.parser.pdf_parser import render_page


def page_elements(text: str, page: int) -> list[Element]:
    elements: list[Element] = []
    table: list[str] = []
    paragraph: list[str] = []

    def flush() -> None:
        if table:
            elements.append(Element(type="Table", text="\n".join(table), page=page))
            table.clear()
        if paragraph:
            elements.append(Element(type="NarrativeText", text="\n".join(paragraph), page=page))
            paragraph.clear()

    for line in text.splitlines():
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            flush()
            elements.append(Element(type="Heading", level=len(heading[1]), text=heading[2], page=page))
        elif line.strip().startswith("|"):
            if paragraph:
                flush()
            table.append(line.strip())
        elif not line.strip():
            flush()
        else:
            if table:
                flush()
            paragraph.append(line.strip())
    flush()
    return elements


async def recognize_missing_pages(
    path: Path, parsed: ParsedContent, gateway: ModelGateway, settings: Settings
) -> ParsedContent:
    recognized = list(parsed.elements)
    for page in parsed.recognition_pages:
        image = render_page(path, page, settings)
        text = await gateway.recognize_page(image)
        elements = page_elements(text, page)
        if not elements:
            raise ValueError(f"第 {page} 页未识别到有效内容")
        recognized.extend(elements)
    return parsed.model_copy(
        update={
            "elements": sorted(recognized, key=lambda element: element.page or 0),
            "recognition_pages": [],
        }
    )
