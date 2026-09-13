"""S3 object-storage adapter using AWS's default credential chain."""

from dataclasses import dataclass
from functools import cached_property
from typing import Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


class StorageUnavailable(Exception):
    """Raised when object storage cannot perform a requested operation."""


class ObjectNotFound(Exception):
    """Raised when the requested object has not reached storage yet."""


@dataclass(frozen=True)
class ObjectMetadata:
    """Small subset of S3 metadata needed to validate an uploaded object."""

    size_bytes: int
    content_type: str


class ObjectStorage(Protocol):
    """Small interface routes need from an object-storage implementation."""

    def create_upload_url(
        self,
        *,
        key: str,
        content_type: str,
        expires_in: int,
    ) -> str: ...

    def get_object_metadata(self, *, key: str) -> ObjectMetadata: ...


class S3ObjectStorage:
    """Sign uploads and inspect objects without proxying file bytes through API."""

    def __init__(
        self, *, bucket: str, region: str, endpoint_url: str | None = None
    ) -> None:
        self.bucket = bucket
        self.region = region
        self.endpoint_url = endpoint_url

    @cached_property
    def _client(self):
        """Create one SDK client on first use, not during application startup."""
        return boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            region_name=self.region,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"} if self.endpoint_url else {},
            ),
        )

    def create_upload_url(
        self,
        *,
        key: str,
        content_type: str,
        expires_in: int,
    ) -> str:
        """Sign permission to PUT one object with the expected content type."""
        if not self.bucket:
            raise StorageUnavailable("S3 bucket is not configured")
        try:
            return self._client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self.bucket,
                    "Key": key,
                    "ContentType": content_type,
                },
                ExpiresIn=expires_in,
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageUnavailable("could not create an upload link") from exc

    def get_object_metadata(self, *, key: str) -> ObjectMetadata:
        """Read object headers so the API can verify a completed upload."""
        if not self.bucket:
            raise StorageUnavailable("S3 bucket is not configured")
        try:
            response = self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code in {"404", "NoSuchKey", "NotFound"}:
                raise ObjectNotFound("uploaded object was not found") from exc
            raise StorageUnavailable("could not inspect the uploaded object") from exc
        except BotoCoreError as exc:
            raise StorageUnavailable("could not inspect the uploaded object") from exc

        try:
            return ObjectMetadata(
                size_bytes=int(response["ContentLength"]),
                content_type=str(response["ContentType"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise StorageUnavailable(
                "storage returned incomplete object metadata"
            ) from exc

    def close(self) -> None:
        """Release the client connection pool if this adapter was used."""
        if "_client" in self.__dict__:
            self._client.close()
