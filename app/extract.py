"""Bounded extraction. A PDF parser runs in a separate, time-limited process."""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from io import BytesIO

MAX_PAGES = 20
MAX_TEXT = 100_000
MAX_LINES = 2_500
MAX_FACTS = 80
PDF_TIMEOUT = 50
FIELD = re.compile(r"^(supplier|po (?:number|ref)|total|item)\s*:\s*(.{1,220})$", re.I)
MONEY = re.compile(r"([A-Z]{3})\s+((?:\d{1,3}(?:,\d{3})+|\d{1,12})\.\d{2})$")
ITEM = re.compile(r"([a-zA-Z0-9_.-]{1,40})\s*\|\s*(\d{1,8})$")
KEYS = {
    "supplier": "supplier",
    "po number": "po_ref",
    "po ref": "po_ref",
    "total": "total",
    "item": "item",
}


class ExtractionError(ValueError):
    pass


def pdf_reader(content: bytes):
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(content))
    if reader.is_encrypted or not 1 <= len(reader.pages) <= MAX_PAGES:
        raise ExtractionError("Use an unencrypted PDF with at most 20 pages.")
    return reader


def render_page(content: bytes, page: int, *, timeout: float = 6) -> bytes:
    if not shutil.which("pdftoppm"):
        raise ExtractionError("PDF page rendering requires Poppler.")
    return run_document_command(
        [
            "pdftoppm",
            "-f",
            str(page),
            "-l",
            str(page),
            "-singlefile",
            "-scale-to",
            "2000",
            "-gray",
            "-png",
            "-",
        ],
        content,
        timeout,
    )


def run_document_command(command: list[str], content: bytes, timeout: float) -> bytes:
    if timeout <= 0:
        raise ExtractionError("PDF extraction exceeded its time budget.")
    try:
        result = subprocess.run(
            command, input=content, capture_output=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        raise ExtractionError("PDF page processing timed out.") from None
    if result.returncode:
        raise ExtractionError("PDF page processing failed.")
    return result.stdout


def pdf_pages(content: bytes) -> tuple[list[str], set[int]]:
    reader = pdf_reader(content)
    deadline = time.monotonic() + PDF_TIMEOUT - 5
    pages = []
    scanned = set()
    size = 0
    for number, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        if not text.strip() and page.images:
            if not shutil.which("tesseract"):
                raise ExtractionError("Scanned PDF extraction requires Tesseract OCR.")
            image = render_page(content, number, timeout=min(6, deadline - time.monotonic()))
            text = run_document_command(
                ["tesseract", "stdin", "stdout", "-l", "eng", "--psm", "6"],
                image,
                min(10, deadline - time.monotonic()),
            ).decode("utf-8")
            scanned.add(number)
        size += len(text)
        if size > MAX_TEXT:
            raise ExtractionError("Document exceeds the 100,000-character extraction limit.")
        pages.append(text)
    return pages, scanned


def field_value(key: str, text: str) -> str | dict[str, str | int] | None:
    text = text.strip()
    if not 1 <= len(text) <= 220 or not text.isprintable():
        return None
    if key == "total":
        money = MONEY.fullmatch(text)
        return {"currency": money[1], "amount": money[2].replace(",", "")} if money else None
    if key == "item":
        item = ITEM.fullmatch(text)
        return {"sku": item[1].upper(), "qty": int(item[2])} if item else None
    return text


def parse(pages: list[str], scanned: set[int] | None = None) -> dict:
    if sum(map(len, pages)) > MAX_TEXT:
        raise ExtractionError("Document exceeds the 100,000-character extraction limit.")
    facts = []
    line_count = 0
    for page_number, page in enumerate(pages, 1):
        for line_number, line in enumerate(page.splitlines(), 1):
            line_count += 1
            if line_count > MAX_LINES:
                raise ExtractionError("Document exceeds the 2,500-line extraction limit.")
            quote = line.strip()
            match = FIELD.fullmatch(quote)
            if not match or not quote.isprintable():
                continue
            label, text = match.groups()
            key = KEYS[label.lower()]
            value = field_value(key, text)
            if value is None:
                continue
            fact = {
                "field": key,
                "value": value,
                "quote": quote,
                "page": page_number,
                "line": line_number,
            }
            if scanned and page_number in scanned:
                fact["source_method"] = "ocr"
            facts.append(fact)
            if len(facts) > MAX_FACTS:
                raise ExtractionError("Document exceeds the 80 recognized field limit.")
    if not any(page.strip() for page in pages):
        raise ExtractionError("No text found in the document.")
    return {"fields": facts}


def extract(content: bytes, media_type: str) -> dict:
    if media_type == "text/plain":
        try:
            return parse([content.decode("utf-8-sig")])
        except UnicodeDecodeError:
            raise ExtractionError("Text uploads must use UTF-8.") from None
    return json.loads(pdf_process(content))["facts"]


def preview_page(content: bytes, page: int) -> bytes:
    return pdf_process(content, "--page", str(page), timeout=10)


def pdf_process(content: bytes, *args: str, timeout: float = PDF_TIMEOUT) -> bytes:
    env = {
        key: os.environ[key]
        for key in ("PATH", "SystemRoot", "WINDIR", "PYTHONPATH")
        if key in os.environ
    }
    env["OMP_THREAD_LIMIT"] = "1"
    try:
        result = subprocess.run(
            [sys.executable, "-m", "app.extract", *args],
            input=content,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise ExtractionError("PDF extraction timed out.") from None
    if result.returncode != 0:
        raise ExtractionError("PDF could not be extracted within safety limits.")
    if result.stdout.startswith(b"EvidenceFlow: "):
        raise ExtractionError(result.stdout.decode("utf-8").removeprefix("EvidenceFlow: ").strip())
    return result.stdout


if __name__ == "__main__":
    if sys.platform.startswith("linux"):
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024**2, 256 * 1024**2))
        resource.setrlimit(resource.RLIMIT_CPU, (15, 16))
    try:
        content = sys.stdin.buffer.read()
        if len(sys.argv) == 3 and sys.argv[1] == "--page":
            page = int(sys.argv[2])
            if not 1 <= page <= len(pdf_reader(content).pages):
                raise ExtractionError("PDF page not found.")
            sys.stdout.buffer.write(render_page(content, page))
        else:
            print(json.dumps({"facts": parse(*pdf_pages(content))}))
    except ExtractionError as exc:
        print(f"EvidenceFlow: {exc}")
    except Exception:
        print("EvidenceFlow: PDF is malformed or unsupported.")
