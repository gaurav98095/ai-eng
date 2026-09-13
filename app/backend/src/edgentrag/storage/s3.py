"""S3 object-storage adapter using AWS's default credential chain."""

from functools import cached_property
from typing import Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


class StorageUnavailable(Exception):
    """Raised when object storage cannot create a signed upload URL."""


class ObjectStorage(Protocol):
    """Small interface routes need from an object-storage implementation."""

    def create_upload_url(
        self,
        *,
        key: str,
        content_type: str,
        expires_in: int,
    ) -> str: ...


class S3ObjectStorage:
    """Create short-lived upload links without proxying file bytes through API."""

    def __init__(self, *, bucket: str, region: str) -> None:
        self.bucket = bucket
        self.region = region

    @cached_property
    def _client(self):
        """Create one SDK client on first use, not during application startup."""
        return boto3.client(
            "s3",
            region_name=self.region,
            config=Config(signature_version="s3v4"),
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

    def close(self) -> None:
        """Release the client connection pool if this adapter was used."""
        if "_client" in self.__dict__:
            self._client.close()
