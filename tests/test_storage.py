from dataclasses import replace
from io import BytesIO

from botocore.response import StreamingBody
from botocore.stub import Stubber

from app.settings import settings
from app.storage import S3Storage


def test_s3_adapter_with_private_bucket_and_bounded_body():
    config = replace(
        settings(),
        s3_endpoint="https://objects.example.invalid",
        s3_bucket="evidence-test",
        s3_access_key="synthetic-key",
        s3_secret_key="synthetic-secret",
        s3_region="auto",
    )
    objects = S3Storage(config)
    key, content = "workspace/case/document", b"PO Number: PO-042"
    with Stubber(objects.client) as service:
        service.add_response("put_object", {}, {"Bucket": config.s3_bucket, "Key": key, "Body": content})
        service.add_response(
            "get_object",
            {"Body": StreamingBody(BytesIO(content), len(content))},
            {"Bucket": config.s3_bucket, "Key": key},
        )
        objects.put(key, content)
        assert objects.get(key) == content
        service.assert_no_pending_responses()
