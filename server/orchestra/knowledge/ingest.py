"""Файл → текст. Документы недоверенные: лимиты размера, страниц и распаковки, разбор в отдельном потоке."""
import asyncio
import io
import zipfile

MAX_FILE = 15 * 1024 * 1024
MAX_UNZIPPED = 80 * 1024 * 1024
MAX_PAGES = 800
TIMEOUT = 60
FORMATS = (".txt", ".md", ".csv", ".docx", ".pdf")


class IngestError(Exception):
    pass


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _docx(data: bytes) -> str:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise IngestError("файл повреждён или это не .docx") from exc
    infos = z.infolist()
    if len(infos) > 3000 or sum(i.file_size for i in infos) > MAX_UNZIPPED:     # защита от zip-бомбы
        raise IngestError("документ распаковывается в слишком большой объём")
    import docx
    d = docx.Document(io.BytesIO(data))
    parts = [p.text for p in d.paragraphs if p.text.strip()]
    for table in d.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                parts.append(" | ".join(dict.fromkeys(cells)))          # объединённые ячейки повторяются
    return "\n\n".join(parts)


def _pdf(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise IngestError("PDF защищён паролем")
        if len(reader.pages) > MAX_PAGES:
            raise IngestError(f"больше {MAX_PAGES} страниц — разделите документ")
        text = "\n\n".join((p.extract_text() or "").strip() for p in reader.pages)
    except PdfReadError as exc:
        raise IngestError("PDF не читается") from exc
    if len(text.strip()) < 20:
        raise IngestError("в PDF нет текстового слоя (скан) — нужен распознанный документ")
    return text


def _extract(name: str, data: bytes) -> str:
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in (".txt", ".md", ".csv"):
        return _decode(data)
    if ext == ".docx":
        return _docx(data)
    if ext == ".pdf":
        return _pdf(data)
    raise IngestError(f"формат {ext or 'без расширения'} не поддерживается: {', '.join(FORMATS)}")


async def extract(name: str, data: bytes) -> str:
    if len(data) > MAX_FILE:
        raise IngestError("файл больше 15 МБ")
    try:
        text = await asyncio.wait_for(asyncio.to_thread(_extract, name, data), timeout=TIMEOUT)
    except TimeoutError as exc:
        raise IngestError("разбор занял больше минуты — документ слишком сложный") from exc
    text = text.replace("\x00", "").strip()
    if not text:
        raise IngestError("в документе нет текста")
    return text
