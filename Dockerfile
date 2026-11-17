FROM ghcr.io/astral-sh/uv:0.11.7 AS uv
FROM python:3.12-slim-bookworm
WORKDIR /srv/evidenceflow
ENV PATH="/srv/evidenceflow/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_LINK_MODE=copy
COPY --from=uv /uv /bin/uv
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-python-downloads \
    && groupadd --gid 10001 evidenceflow \
    && useradd --uid 10001 --gid 10001 --no-create-home evidenceflow \
    && install -d -o 10001 -g 10001 /srv/evidenceflow/data
COPY app ./app
COPY migrations ./migrations
COPY web ./web
COPY examples ./examples
USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "app.serve"]
