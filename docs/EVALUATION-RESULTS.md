# EvidenceFlow 0.2.0 benchmark baseline

Measured with Docker Compose, Python 3.12.16, Tesseract 5.3.0 with English language data, and Poppler 22.12.0.

| Metric | Matches / expected | Predicted | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Field exact match | 53 / 53 | 53 | 1.00 | 1.00 | 1.00 |
| OCR field exact match | 7 / 7 | 7 | 1.00 | 1.00 | 1.00 |
| Finding detection | 6 / 6 | 6 | 1.00 | 1.00 | 1.00 |
| Source citations | 12 / 12 | 12 | 1.00 | 1.00 | 1.00 |

All five case states matched the labels. The nine unique synthetic files include two image-only scans. Some files are reused across cases, so field counts cover 14 document instances. These are fixture-level regression results for supported English labels. See [evaluation methodology and reproduction](EVALUATION.md).

The release also passed 16 tests in Linux containers against PostgreSQL 18. The browser workflow on desktop and mobile covered scanned uploads, the page 2 preview, reviewer corrections and history, refreshed MCP findings, and a stale approval proposal.
