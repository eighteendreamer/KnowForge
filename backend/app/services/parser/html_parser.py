import re
from pathlib import Path

from bs4 import BeautifulSoup, Tag, UnicodeDammit

from app.schemas.documents import Element, ParsedContent


class ParseError(ValueError):
    pass


def markdown_table(rows: list[list[str]]) -> str:
    width = max((len(row) for row in rows), default=0)
    if not width:
        return ""
    normalized = [
        [cell.replace("|", "\\|").replace("\n", " ").strip() for cell in row] + [""] * (width - len(row))
        for row in rows
    ]
    lines = ["| " + " | ".join(row) + " |" for row in normalized]
    lines.insert(1, "| " + " | ".join(["---"] * width) + " |")
    return "\n".join(lines)


def parse_html(path: Path) -> ParsedContent:
    content = path.read_bytes()
    decoded = UnicodeDammit(content, is_html=True).unicode_markup
    if not decoded or "\x00" in decoded:
        raise ParseError("HTML 内容为空或编码无效")
    soup = BeautifulSoup(decoded, "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else path.stem
    for node in soup.select(
        "script, style, nav, header, footer, aside, iframe, object, embed, form, [hidden], [aria-hidden=true]"
    ):
        node.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    elements: list[Element] = []

    def visit(node: Tag) -> None:
        name = node.name or ""
        if name == "table":
            rows = [
                [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"], recursive=False)]
                for row in node.find_all("tr")
            ]
            value = markdown_table([row for row in rows if row])
            if value:
                elements.append(Element(type="Table", text=value))
            return
        if re.fullmatch(r"h[1-6]", name):
            text = node.get_text(" ", strip=True)
            if text:
                elements.append(Element(type="Heading", level=int(name[1]), text=text))
            return
        if name in {"p", "li", "pre", "blockquote"}:
            text = node.get_text("\n" if name == "pre" else " ", strip=True)
            if text:
                element_type = "Code" if name == "pre" else "ListItem" if name == "li" else "NarrativeText"
                elements.append(Element(type=element_type, text=text))
            return
        direct_text: list[str] = []
        for child in node.children:
            if isinstance(child, Tag):
                if direct_text:
                    text = " ".join(direct_text).strip()
                    if text:
                        elements.append(Element(type="NarrativeText", text=text))
                    direct_text.clear()
                visit(child)
            elif str(child).strip():
                direct_text.append(str(child).strip())
        if direct_text:
            elements.append(Element(type="NarrativeText", text=" ".join(direct_text)))

    visit(root)
    if not elements:
        raise ParseError("HTML 未提取到正文")
    return ParsedContent(title=title[:500], elements=elements, total_pages=None)
