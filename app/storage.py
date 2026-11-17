from pathlib import Path
from uuid import UUID, uuid4

import boto3
from botocore.config import Config

from app.settings import MAX_UPLOAD, Settings


def object_key(workspace_id: UUID, case_id: UUID, document_id: UUID) -> str:
    return f"{workspace_id}/{case_id}/{document_id}"


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def put(self, key: str, content: bytes):
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{uuid4()}.tmp")
        try:
            temporary.write_bytes(content)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def get(self, key: str) -> bytes:
        with (self.root / key).open("rb") as stream:
            return checked_size(stream.read(MAX_UPLOAD + 1))


class S3Storage:
    def __init__(self, config: Settings):
        self.bucket = config.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=config.s3_endpoint,
            region_name=config.s3_region,
            aws_access_key_id=config.s3_access_key,
            aws_secret_access_key=config.s3_secret_key,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=3,
                read_timeout=8,
                retries={"total_max_attempts": 1},
            ),
        )

    def put(self, key: str, content: bytes):
        self.client.put_object(Bucket=self.bucket, Key=key, Body=content)

    def get(self, key: str) -> bytes:
        body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
        try:
            return checked_size(body.read(MAX_UPLOAD + 1))
        finally:
            body.close()


def checked_size(content: bytes) -> bytes:
    if len(content) > MAX_UPLOAD:
        raise ValueError("Stored object exceeds upload limit")
    return content


def storage(config: Settings) -> LocalStorage | S3Storage:
    return S3Storage(config) if config.storage_backend == "s3" else LocalStorage(config.storage_dir)
