import shutil

import pytest

from app.extract import extract
from app.settings import ROOT


@pytest.mark.skipif(
    not shutil.which("pdftoppm") or not shutil.which("tesseract"),
    reason="Install Poppler and Tesseract for scanned-PDF testing.",
)
def test_scanned_invoice_uses_ocr_with_page_and_line():
    content = (ROOT / "examples" / "ocr" / "northline-invoice.pdf").read_bytes()
    facts = extract(content, "application/pdf")["fields"]
    total = next(fact for fact in facts if fact["field"] == "total")
    assert total["value"] == {"currency": "USD", "amount": "1320.00"}
    assert total["source_method"] == "ocr"
    assert total["page"] == 2
    assert total["line"] > 0
    assert "1320.00" in total["quote"]
