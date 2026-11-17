import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
MAX_UPLOAD = 5 * 1024 * 1024
MAX_DOCUMENTS = 20


def flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() == "true"


@dataclass(frozen=True)
class Settings:
    database_url: str
    origin: str
    secure_cookie: bool
    storage_backend: str
    storage_dir: Path
    s3_endpoint: str
    s3_bucket: str
    s3_access_key: str
    s3_secret_key: str
    s3_region: str
    worker_enabled: bool
    hf_token: str
    hf_model: str
    hf_allow_evidence: bool


def settings() -> Settings:
    load_dotenv(ROOT / ".env")
    origin = os.getenv("APP_ORIGIN", "http://localhost:8080").rstrip("/")
    parsed = urlsplit(origin)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or (parsed.path or parsed.query or parsed.fragment or parsed.username)
    ):
        raise ValueError("APP_ORIGIN must be a single HTTP(S) origin")
    secure = flag("COOKIE_SECURE", parsed.scheme == "https")
    if not secure and (parsed.scheme == "https" or parsed.hostname not in {"localhost", "127.0.0.1"}):
        raise ValueError("Public origins require HTTPS and COOKIE_SECURE=true")
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("Public origins require HTTPS")
    backend = os.getenv("STORAGE_BACKEND", "local")
    endpoint = os.getenv("S3_ENDPOINT", "")
    bucket = os.getenv("S3_BUCKET", "")
    access_key = os.getenv("S3_ACCESS_KEY_ID", "")
    secret_key = os.getenv("S3_SECRET_ACCESS_KEY", "")
    if backend not in {"s3", "local"}:
        raise ValueError("STORAGE_BACKEND must be local or s3")
    if backend == "s3" and (
        not endpoint.startswith("https://") or not all([bucket, access_key, secret_key])
    ):
        raise ValueError("S3 requires an HTTPS endpoint, bucket and scoped credentials")
    return Settings(
        database_url=os.getenv(
            "DATABASE_URL", "postgresql://evidenceflow:evidenceflow@127.0.0.1:5432/evidenceflow"
        ),
        origin=origin,
        secure_cookie=secure,
        storage_backend=backend,
        storage_dir=Path(os.getenv("STORAGE_DIR", "./data/objects")),
        s3_endpoint=endpoint,
        s3_bucket=bucket,
        s3_access_key=access_key,
        s3_secret_key=secret_key,
        s3_region=os.getenv("S3_REGION", "auto"),
        worker_enabled=flag("WORKER_ENABLED", True),
        hf_token=os.getenv("HF_TOKEN", ""),
        hf_model=os.getenv("HF_MODEL", "openai/gpt-oss-120b:fastest"),
        hf_allow_evidence=flag("HF_ALLOW_EVIDENCE"),
    )
