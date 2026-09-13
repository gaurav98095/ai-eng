"""Unit tests for the S3 adapter's signing contract."""

from unittest.mock import Mock, patch

import pytest
from botocore.exceptions import ClientError

from edgentrag.storage.s3 import (
    ObjectMetadata,
    ObjectNotFound,
    S3ObjectStorage,
    StorageUnavailable,
)


def test_s3_adapter_signs_one_put_with_bound_content_type() -> None:
    client = Mock()
    client.generate_presigned_url.return_value = "https://signed.test/object"

    with patch("edgentrag.storage.s3.boto3.client", return_value=client) as factory:
        storage = S3ObjectStorage(
            bucket="private-bucket",
            region="ap-south-1",
            endpoint_url="http://localhost:4566",
        )
        url = storage.create_upload_url(
            key="uploads/session/file-id",
            content_type="application/pdf",
            expires_in=900,
        )

    factory.assert_called_once()
    call_kwargs = factory.call_args.kwargs
    assert call_kwargs["endpoint_url"] == "http://localhost:4566"
    assert call_kwargs["region_name"] == "ap-south-1"
    assert call_kwargs["config"].s3 == {"addressing_style": "path"}
    client.generate_presigned_url.assert_called_once_with(
        "put_object",
        Params={
            "Bucket": "private-bucket",
            "Key": "uploads/session/file-id",
            "ContentType": "application/pdf",
        },
        ExpiresIn=900,
    )
    assert url == "https://signed.test/object"
    storage.close()


def test_s3_adapter_fails_clearly_when_bucket_is_not_configured() -> None:
    storage = S3ObjectStorage(bucket="", region="ap-south-1")

    with pytest.raises(StorageUnavailable, match="bucket is not configured"):
        storage.create_upload_url(
            key="uploads/session/file-id",
            content_type="text/plain",
            expires_in=900,
        )


def test_s3_adapter_reads_object_metadata() -> None:
    client = Mock()
    client.head_object.return_value = {
        "ContentLength": 4096,
        "ContentType": "application/pdf",
    }

    with patch("edgentrag.storage.s3.boto3.client", return_value=client):
        storage = S3ObjectStorage(bucket="private-bucket", region="us-east-1")
        metadata = storage.get_object_metadata(key="uploads/session/file-id")

    assert metadata == ObjectMetadata(size_bytes=4096, content_type="application/pdf")
    client.head_object.assert_called_once_with(
        Bucket="private-bucket",
        Key="uploads/session/file-id",
    )
    storage.close()


def test_s3_adapter_reports_an_object_that_has_not_arrived() -> None:
    client = Mock()
    client.head_object.side_effect = ClientError(
        {"Error": {"Code": "404", "Message": "Not Found"}},
        "HeadObject",
    )

    with patch("edgentrag.storage.s3.boto3.client", return_value=client):
        storage = S3ObjectStorage(bucket="private-bucket", region="us-east-1")
        with pytest.raises(ObjectNotFound, match="object was not found"):
            storage.get_object_metadata(key="uploads/session/file-id")

    storage.close()
