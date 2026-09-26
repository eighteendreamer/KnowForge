import re
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from tokenizers import Tokenizer

from app.schemas.documents import ChunkData, Element
from app.services.parser.html_parser import ParseError


class Chunker:
    def __init__(self, tokenizer_path: Path, size: int = 600, overlap: int = 80, model_limit: int = 8192):
        if not 0 <= overlap < size <= model_limit:
            raise ValueError("Invalid chunk/token limits")
        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self.size = size
        self.overlap = overlap
        self.model_limit = model_limit

    def count(self, text: str) -> int:
        return len(self.tokenizer.encode(text, add_special_tokens=False).ids)

    def split_text(self, text: str, budget: int) -> list[str]:
        if budget <= self.overlap:
            raise ParseError("标题上下文超过分块预算")
        result: list[str] = []
        start = 0
        while start < len(text):
            tail = text[start:]
            encoding = self.tokenizer.encode(tail, add_special_tokens=False)
            if len(encoding.ids) <= budget:
                if tail.strip():
                    result.append(tail.strip())
                break
            end = encoding.offsets[budget][0]
            if end <= 0:
                raise ParseError("分词器无法确定分块边界")
            candidate = tail[:end]
            for pattern in (r"\n\s*\n", r"[。！？!?]\s*", r"[；;]\s*", r"\s+"):
                boundaries = [match.end() for match in re.finditer(pattern, candidate)]
                if boundaries and boundaries[-1] >= end // 2:
                    end = boundaries[-1]
                    break
            piece = tail[:end].strip()
            if piece:
                result.append(piece)
            tokens = self.tokenizer.encode(tail[:end], add_special_tokens=False)
            overlap_start = tokens.offsets[max(0, len(tokens.ids) - self.overlap)][0] if self.overlap else end
            start += max(1, overlap_start)
        return result

    def build(self, doc_id: str, elements: list[Element], source: str) -> list[ChunkData]:
        chunks: list[ChunkData] = []
        headings: list[tuple[int, str]] = []
        buffer: list[Element] = []

        def emit(text: str, group: list[Element], section: list[str], is_table: bool = False) -> None:
            prefix = "[" + " > ".join(section) + "] " if section else ""
            contextual = prefix + text
            if self.count(contextual) > self.model_limit:
                raise ParseError("表格或分块超过模型输入上限，需人工调整")
            pages = [element.page for element in group if element.page is not None]
            index = len(chunks)
            types = list(dict.fromkeys(element.type for element in group))
            metadata = {"source": source, "tags": [], "category": "", "difficulty": ""}
            if is_table:
                metadata["table_summary"] = "\n".join(text.splitlines()[:4])
            chunks.append(
                ChunkData(
                    chunk_id="chunk_" + uuid5(NAMESPACE_URL, f"knowforge:{doc_id}:{index}").hex,
                    doc_id=doc_id,
                    chunk_index=index,
                    section_path=section,
                    text=text,
                    text_with_context=contextual,
                    page_start=min(pages) if pages else None,
                    page_end=max(pages) if pages else None,
                    char_count=len(text),
                    token_count=self.count(contextual),
                    element_types=types,
                    metadata=metadata,
                )
            )

        def flush() -> None:
            if not buffer:
                return
            section = [heading[1] for heading in headings]
            prefix = "[" + " > ".join(section) + "] " if section else ""
            budget = self.size - self.count(prefix) - 2
            text = "\n\n".join(element.text for element in buffer)
            for piece in self.split_text(text, budget):
                emit(piece, buffer, section)
            buffer.clear()

        for element in elements:
            if element.type == "Heading":
                flush()
                level = element.level or 1
                while headings and headings[-1][0] >= level:
                    headings.pop()
                headings.append((level, element.text))
            elif element.type == "Table":
                flush()
                emit(element.text, [element], [heading[1] for heading in headings], is_table=True)
            else:
                if buffer and self.count("\n\n".join(item.text for item in [*buffer, element])) > self.size:
                    flush()
                buffer.append(element)
        flush()
        if not chunks:
            raise ParseError("文档没有可分块的正文")
        return chunks
