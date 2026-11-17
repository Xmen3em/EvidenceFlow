# Evaluation

Run the release benchmark from the repository root after starting Compose.

```sh
docker compose exec -T app python -m app.evaluate --check
```

The same command runs in CI with Tesseract and Poppler installed. Run it without `--check` to print the report while experimenting with extraction rules.

## Dataset

The labeled manifest in `examples/evaluation.json` defines five synthetic cases with nine unique files and 14 document instances. Two files are image-only English PDFs. The invoice places its fields on page 2, and the receipt uses a slightly blurred, rotated raster. The cases cover discrepant totals, a quantity difference, a missing receipt, supported fields that agree, and ambiguous totals.

The evaluator runs the application extractor and review rules. Gold labels specify document fields, expected finding codes, source filenames, pages and quotes, and the expected case state.

| Metric | Counting rule |
| --- | --- |
| Field exact match | Match the case, document, field key and normalized value. Duplicate values count as separate occurrences. |
| OCR field exact match | Apply the same comparison to the scanned fixtures. The source must be marked as OCR. |
| Finding detection | Match each case and finding code. |
| Source citations | Match the finding code, filename, page and quote after normalizing whitespace. The check also requires a positive extracted line number. |
| Case state | Compare the reported review state with the expected state. |

The report includes precision, recall and F1. A false positive reduces precision and a missed label reduces recall. `--check` exits with an error if any score differs from the gold labels, a case state differs, or an extraction fails.

[See the measured baseline](EVALUATION-RESULTS.md).

The baseline characterizes these synthetic fixtures and the supported English field labels. It does not estimate accuracy on vendor layouts, handwriting or arbitrary scan quality. It measures the extraction and deterministic comparison pipeline. Hugging Face commentary is covered by separate integration tests.

## Portfolio walkthrough

1. Create a new case and upload `examples/PO-042-po.txt`, `examples/ocr/northline-invoice.pdf` and `examples/ocr/northline-receipt.pdf` with the matching document types.
2. Open the invoice total finding. Select **Inspect source page** on the invoice citation to open page 2. Show the OCR transcription and the page image together.
3. As the analyst, propose a follow-up. As the reviewer, open the invoice total from **Documents** and record a correction. For the demo, you can temporarily enter `USD 1200.00` to show how an incorrect interpretation would affect the findings, then restore `USD 1320.00`. Describe both edits as a synthetic workflow demonstration.
4. Show the correction history, the stale proposal and the new MCP review in **Activity**. Run the benchmark and show the denominator next to each score.
