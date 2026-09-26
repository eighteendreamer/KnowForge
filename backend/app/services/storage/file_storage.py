import asyncio
from pathlib import Path
from shutil import rmtree
from uuid import UUID, uuid4

import filetype
from fastapi import UploadFile

from app.core.config import Settings
from app.core.errors import AppError

# One part is capped well below upload_max_bytes so the browser can retry a single slice.
CHUNK_BYTES = 8 * 1024 * 1024
MAGIC_BYTES = 512


class LocalStorage:
    def __init__(self, settings: Settings):
        self.root = settings.storage_path.resolve()
        self.max_bytes = settings.upload_max_bytes

    def resolve(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise AppError(400, 1001, "无效的文件存储路径")
        return path

    async def save_upload(self, upload: UploadFile) -> tuple[str, int, str]:
        content = await upload.read(self.max_bytes + 1)
        file_type = self.detect(
            filename=upload.filename or "",
            content_type=upload.content_type,
            size=len(content),
            magic=content[:MAGIC_BYTES],
            has_null_byte=b"\x00" in content,
        )
        key = f"documents/{uuid4().hex}.{file_type}"
        path = self.resolve(key)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, content)
        return key, len(content), file_type

    def detect(
        self, filename: str, content_type: str | None, size: int, magic: bytes, has_null_byte: bool
    ) -> str:
        suffix = Path(filename.replace("\\", "/").rsplit("/", 1)[-1]).suffix.lower()
        if suffix not in {".pdf", ".html", ".htm"}:
            raise AppError(400, 1001, "仅支持 PDF 和 HTML 文件")
        if not size or size > self.max_bytes:
            raise AppError(400, 1001, f"文件为空或超过 {self.max_bytes // 1048576}MB 限制")
        detected = filetype.guess_mime(magic)
        if suffix == ".pdf":
            if detected != "application/pdf" or content_type not in {
                "application/pdf",
                "application/octet-stream",
            }:
                raise AppError(400, 1001, "PDF 文件格式与声明不匹配")
            return "pdf"
        if (
            detected is not None
            or has_null_byte
            or content_type
            not in {
                "text/html",
                "application/xhtml+xml",
                "application/octet-stream",
            }
        ):
            raise AppError(400, 1001, "HTML 文件格式与声明不匹配")
        return "html"

    def part_path(self, upload_id: str, part_number: int) -> Path:
        try:
            # Browsers and pydantic may spell the same uuid with or without hyphens; store one form.
            directory = str(UUID(upload_id))
        except ValueError:
            raise AppError(400, 1001, "分片上传标识无效") from None
        if not 1 <= part_number <= self.max_bytes // CHUNK_BYTES + 1:
            raise AppError(400, 1001, "分片序号超出范围")
        return self.resolve(f"uploads/{directory}/{part_number:05d}")

    async def save_part(self, upload_id: str, part_number: int, upload: UploadFile) -> None:
        content = await upload.read(CHUNK_BYTES + 1)
        if not content or len(content) > CHUNK_BYTES:
            raise AppError(400, 1001, f"分片为空或超过 {CHUNK_BYTES // 1048576}MB")
        path = self.part_path(upload_id, part_number)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, content)

    def join_parts(self, upload_id: str, joined: Path, total_parts: int) -> tuple[int, bytes, bool]:
        size, magic, has_null_byte = 0, bytearray(), False
        joined.parent.mkdir(parents=True, exist_ok=True)
        with joined.open("wb") as sink:
            for part_number in range(1, total_parts + 1):
                part = self.part_path(upload_id, part_number)
                if not part.is_file():
                    raise AppError(400, 1001, f"缺少第 {part_number} 个分片")
                with part.open("rb") as source:
                    while chunk := source.read(1 << 20):
                        size += len(chunk)
                        if size > self.max_bytes:
                            raise AppError(400, 1001, "文件超过大小限制")
                        if len(magic) < MAGIC_BYTES:
                            magic += chunk[: MAGIC_BYTES - len(magic)]
                        has_null_byte = has_null_byte or b"\x00" in chunk
                        sink.write(chunk)
        return size, bytes(magic), has_null_byte

    async def assemble(
        self, upload_id: str, filename: str, content_type: str | None, total_parts: int
    ) -> tuple[str, int, str]:
        directory = self.part_path(upload_id, 1).parent
        joined = directory / "assembled"
        try:
            size, magic, has_null_byte = await asyncio.to_thread(
                self.join_parts, upload_id, joined, total_parts
            )
            file_type = self.detect(
                filename=filename,
                content_type=content_type,
                size=size,
                magic=magic,
                has_null_byte=has_null_byte,
            )
            key = f"documents/{uuid4().hex}.{file_type}"
            target = self.resolve(key)
            await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(joined.replace, target)
            return key, size, file_type
        finally:
            await asyncio.to_thread(rmtree, directory, True)

    def write_json(self, key: str, content: str) -> None:
        path = self.resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)

    def delete(self, key: str) -> None:
        self.resolve(key).unlink(missing_ok=True)
