from uuid import uuid4

import pytest

from app.extract import ExtractionError, extract, parse
from app.rules import review
from app.settings import ROOT


def doc(kind, text):
    return {
        "id": uuid4(),
        "kind": kind,
        "filename": f"{kind}.txt",
        "status": "ready",
        "facts": parse([text]),
    }


def fixture(ref, kind):
    return doc(kind, (ROOT / "examples" / f"{ref}-{kind}.txt").read_text(encoding="utf-8"))


def result(*docs):
    return review({"id": uuid4(), "revision": 3}, list(docs))


def test_difference_cites_original_lines():
    output = result(*(fixture("PO-042", kind) for kind in ("po", "invoice", "receipt")))
    assert output["state"] == "differences_found"
    codes = {item["code"]: item for item in output["findings"]}
    assert set(codes) == {"total_differs", "quantity_differs_CMP-KIT"}
    assert [(src["page"], src["line"], src["quote"]) for src in codes["total_differs"]["sources"]] == [
        (1, 4, "Total: USD 1,200.00"),
        (1, 4, "Total: USD 1,320.00"),
    ]
    assert "does not verify" in output["scope"]


def test_equal_po_and_invoice_still_need_receipt():
    output = result(fixture("PO-088", "po"), fixture("PO-088", "invoice"))
    assert output["state"] == "needs_evidence"
    assert [item["code"] for item in output["findings"]] == ["missing_receipt"]


def test_multiple_documents_and_duplicate_lines_are_ambiguous():
    order = fixture("PO-042", "po")
    invoice = doc(
        "invoice",
        "Supplier: Northline Industrial\nPO Ref: PO-042\nTotal: EUR 1200.00\nItem: CMP-KIT | 5\nItem: CMP-KIT | 7",
    )
    receipt = fixture("PO-042", "receipt")
    output = result(order, invoice, receipt)
    assert {f["code"] for f in output["findings"]} == {"currency_differs", "duplicate_item_CMP-KIT"}
    output = result(order, fixture("PO-088", "po"), invoice, receipt)
    assert "multiple_po" in {f["code"] for f in output["findings"]}


def test_three_present_documents_without_supported_fields_do_not_count_as_complete():
    output = result(*(doc(kind, "Unsupported format") for kind in ("po", "invoice", "receipt")))
    assert output["state"] == "needs_evidence"
    assert len(output["findings"]) == 11


def test_bounded_text_and_encoding():
    with pytest.raises(ExtractionError, match="UTF-8"):
        extract(b"\xff", "text/plain")
    with pytest.raises(ExtractionError, match="80"):
        parse(["Item: ABC | 1\n" * 81])
    with pytest.raises(ExtractionError, match="100,000"):
        parse(["x" * 100_001])
    with pytest.raises(ExtractionError, match="No text"):
        parse([""])


def tiny_pdf(line):
    stream = f"BT /F1 12 Tf 10 70 Td ({line}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 240 100] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    document = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, data in enumerate(objects, 1):
        offsets.append(len(document))
        document.extend(f"{number} 0 obj\n".encode() + data + b"\nendobj\n")
    start = len(document)
    document.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets:
        document.extend(f"{offset:010} 00000 n \n".encode())
    document.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode())
    return bytes(document)


def test_pdf_subprocess_extracts_page_and_line():
    data = extract(tiny_pdf("Total: USD 123.00"), "application/pdf")
    assert data["fields"] == [
        {
            "field": "total",
            "value": {"currency": "USD", "amount": "123.00"},
            "quote": "Total: USD 123.00",
            "page": 1,
            "line": 1,
        }
    ]
    with pytest.raises(ExtractionError, match="malformed"):
        extract(b"%PDF-broken", "application/pdf")
