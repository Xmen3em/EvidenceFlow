from decimal import Decimal
from itertools import combinations

LABELS = {"po": "purchase order", "invoice": "invoice", "receipt": "goods receipt"}


def review(case: dict, documents: list[dict]) -> dict:
    findings = []
    selected = {}

    def add(code, level, message, sources=()):
        findings.append({"code": code, "level": level, "message": message, "sources": list(sources)})

    for kind, label in LABELS.items():
        group = [doc for doc in documents if doc["kind"] == kind]
        if not group:
            add(f"missing_{kind}", "gap", f"Add a {label} to complete the evidence set.")
        elif len(group) > 1:
            add(f"multiple_{kind}", "gap", f"Multiple {label} documents need manual selection.")
        elif group[0]["status"] != "ready":
            add(f"unavailable_{kind}", "gap", f"The {label} has no completed extraction.")
        else:
            doc = group[0]
            selected[kind] = [
                {**fact, "document_id": str(doc["id"]), "filename": doc["filename"], "kind": kind}
                for fact in doc["facts"]["fields"]
            ]

    def value(kind, field):
        if kind not in selected:
            return None
        hits = [fact for fact in selected[kind] if fact["field"] == field]
        if len(hits) != 1:
            add(
                f"{field}_{kind}_ambiguous",
                "gap",
                f"Need one unambiguous {field.replace('_', ' ')} on the {LABELS[kind]}.",
                hits[:4],
            )
            return None
        return hits[0]

    for field in ("supplier", "po_ref"):
        facts = [fact for kind in LABELS if (fact := value(kind, field))]
        for left, right in combinations(facts, 2):
            if left["value"].casefold() != right["value"].casefold():
                add(
                    f"{field}_{left['kind']}_{right['kind']}",
                    "difference",
                    f"{field.replace('_', ' ').capitalize()} differs between the {LABELS[left['kind']]} and {LABELS[right['kind']]}.",
                    (left, right),
                )

    po_total = value("po", "total")
    invoice_total = value("invoice", "total")
    if po_total and invoice_total:
        left, right = po_total["value"], invoice_total["value"]
        if left["currency"] != right["currency"]:
            add(
                "currency_differs",
                "difference",
                "The PO and invoice use different currencies.",
                (po_total, invoice_total),
            )
        elif Decimal(left["amount"]) != Decimal(right["amount"]):
            add(
                "total_differs",
                "difference",
                "The invoice total differs from the PO total.",
                (po_total, invoice_total),
            )

    grouped = {}
    for kind, facts in selected.items():
        items = [fact for fact in facts if fact["field"] == "item"]
        if not items:
            add(
                f"no_items_{kind}",
                "gap",
                f"No supported line items were extracted from the {LABELS[kind]}.",
            )
        for fact in items:
            grouped.setdefault(fact["value"]["sku"], {}).setdefault(kind, []).append(fact)
    for sku, by_kind in sorted(grouped.items()):
        sources = [fact for facts in by_kind.values() for fact in facts]
        if any(len(facts) > 1 for facts in by_kind.values()):
            add(
                f"duplicate_item_{sku}",
                "gap",
                f"{sku} appears more than once on a document.",
                sources[:6],
            )
            continue
        missing = [LABELS[kind] for kind in selected if kind not in by_kind]
        if missing:
            add(
                f"missing_item_{sku}",
                "gap",
                f"{sku} has no extracted line on: {', '.join(missing)}.",
                sources,
            )
        if len({fact["value"]["qty"] for fact in sources}) > 1:
            add(
                f"quantity_differs_{sku}",
                "difference",
                f"Extracted quantities disagree for {sku}.",
                sources,
            )

    state = "needs_evidence" if any(f["level"] == "gap" for f in findings) else "ready_for_review"
    if any(f["level"] == "difference" for f in findings):
        state = "differences_found"
    return {
        "case_id": str(case["id"]),
        "revision": case["revision"],
        "state": state,
        "documents": len(documents),
        "findings": findings,
        "scope": "Preliminary evidence review. It does not verify a three-way match or authorize payment.",
    }
