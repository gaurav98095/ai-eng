"""Create the queues required by the local Compose stack, if absent."""

import json
import os

import boto3
from botocore.exceptions import ClientError


QUEUE_NAMES = (
    "edgentrag-local-ingestion",
    "edgentrag-local-chat",
    "edgentrag-local-stt",
    "edgentrag-local-embedding",
)
BUCKET_NAME = "edgentrag-local"


def aws_client_kwargs() -> dict[str, str]:
    return {
        "region_name": os.getenv("EDGENTRAG_AWS_REGION", "us-east-1"),
        "endpoint_url": os.getenv("EDGENTRAG_AWS_ENDPOINT_URL", "http://floci:4566"),
        "aws_access_key_id": os.getenv("AWS_ACCESS_KEY_ID", "test"),
        "aws_secret_access_key": os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
    }


def main() -> None:
    kwargs = aws_client_kwargs()
    client = boto3.client("sqs", **kwargs)
    s3 = boto3.client("s3", **kwargs)
    try:
        s3.create_bucket(Bucket=BUCKET_NAME)
        print(f"S3 bucket ready: {BUCKET_NAME}")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in {
            "BucketAlreadyOwnedByYou",
            "BucketAlreadyExists",
        }:
            raise
        print(f"S3 bucket ready: {BUCKET_NAME}")
    for name in QUEUE_NAMES:
        dead_letter = client.create_queue(QueueName=f"{name}-dlq")
        dead_letter_url = dead_letter["QueueUrl"]
        attributes = client.get_queue_attributes(
            QueueUrl=dead_letter_url,
            AttributeNames=["QueueArn"],
        )["Attributes"]
        result = client.create_queue(QueueName=name)
        queue_url = result["QueueUrl"]
        client.set_queue_attributes(
            QueueUrl=queue_url,
            Attributes={
                "RedrivePolicy": json.dumps(
                    {
                        "deadLetterTargetArn": attributes["QueueArn"],
                        "maxReceiveCount": os.getenv("QUEUE_MAX_RECEIVES", "5"),
                    }
                )
            },
        )
        print(f"SQS queue ready: {queue_url} (DLQ: {dead_letter_url})")


if __name__ == "__main__":
    main()
