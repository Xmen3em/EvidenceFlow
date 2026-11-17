"""Score the production extractor and review rules against synthetic gold labels."""

import argparse
import json
import re
from collections import Counter
from uuid import NAMESPACE_URL, uuid5

from app.extract import ExtractionError, extract
from app.rules import review
from app.settings import ROOT

FIXTURES = ROOT / "examples"
MANIFEST = FIXTURES / "evaluation.json"


def signature(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def scores(expected: Counter, predicted: Counter) -> dict:
    matched = sum((expected & predicted).values())
    gold, observed = sum(expected.values()), sum(predicted.values())
    precision = matched / observed if observed else float(not gold)
    recall = matched / gold if gold else 1.0
    return {
        "matched": matched,
        "expected": gold,
        "predicted": observed,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0,
    }


def citation_signature(source):
    quote = re.sub(r"\s+", " ", source["quote"]).strip()
    return source["filename"], source["page"], quote


def evaluate(manifest: dict) -> dict:
    gold_fields, actual_fields = Counter(), Counter()
    gold_scans, actual_scans = Counter(), Counter()
    gold_findings, actual_findings = Counter(), Counter()
    gold_citations, actual_citations = Counter(), Counter()
    outcomes = []
    checked_documents = set()
    for case in manifest["cases"]:
        documents = []
        for name in case["documents"]:
            sample = manifest["documents"][name]
            checked_documents.add(name)
            path = FIXTURES / name
            media_type = "application/pdf" if path.suffix == ".pdf" else "text/plain"
            extraction_failed = False
            try:
                facts = extract(path.read_bytes(), media_type)
            except ExtractionError:
                facts, extraction_failed = {"fields": []}, True
            documents.append(
                {
                    "id": uuid5(NAMESPACE_URL, name),
                    "filename": name,
                    "kind": sample["kind"],
                    "facts": facts,
                    "status": "failed" if extraction_failed else "ready",
                }
            )
            expected = Counter(
                (case["id"], name, field, signature(value)) for field, value in sample["fields"]
            )
            predicted = Counter(
                (case["id"], name, fact["field"], signature(fact["value"])) for fact in facts["fields"]
            )
            gold_fields.update(expected)
            actual_fields.update(predicted)
            if sample.get("scan"):
                gold_scans.update(expected)
                actual_scans.update(
                    (case["id"], name, fact["field"], signature(fact["value"]))
                    for fact in facts["fields"]
                    if fact.get("source_method") == "ocr"
                )

        result = review({"id": uuid5(NAMESPACE_URL, case["id"]), "revision": 0}, documents)
        expected = case["findings"]
        gold_findings.update((case["id"], code) for code in expected)
        actual_findings.update((case["id"], finding["code"]) for finding in result["findings"])
        for code, sources in expected.items():
            gold_citations.update((case["id"], code, *source) for source in sources)
        for finding in result["findings"]:
            actual_citations.update(
                (case["id"], finding["code"], *citation_signature(source))
                for source in finding["sources"]
                if source["line"] > 0
            )
        outcomes.append(
            {
                "case": case["id"],
                "expected_state": case["state"],
                "state": result["state"],
                "expected_findings": sorted(expected),
                "findings": sorted(item["code"] for item in result["findings"]),
                "extraction_failures": [
                    doc["filename"] for doc in documents if doc["status"] == "failed"
                ],
            }
        )

    return {
        "dataset": manifest["dataset"],
        "cases": len(outcomes),
        "unique_documents": len(checked_documents),
        "document_instances": sum(len(case["documents"]) for case in manifest["cases"]),
        "metrics": {
            "field_exact_match": scores(gold_fields, actual_fields),
            "ocr_field_exact_match": scores(gold_scans, actual_scans),
            "finding_detection": scores(gold_findings, actual_findings),
            "source_citations": scores(gold_citations, actual_citations),
        },
        "state_matches": sum(case["state"] == case["expected_state"] for case in outcomes),
        "outcomes": outcomes,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Fail if the synthetic regression cases differ."
    )
    args = parser.parse_args()
    report = evaluate(json.loads(MANIFEST.read_text(encoding="utf-8")))
    print(json.dumps(report, indent=2))
    if args.check and (
        any(
            item["matched"] != item["expected"] or item["matched"] != item["predicted"]
            for item in report["metrics"].values()
        )
        or report["state_matches"] != report["cases"]
        or any(case["extraction_failures"] for case in report["outcomes"])
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
