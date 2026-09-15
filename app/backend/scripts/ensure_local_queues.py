"""Create the queues required by the local Compose stack, if absent."""

import os

import boto3


QUEUE_NAMES = (
    "edgentrag-local-ingestion",
    "edgentrag-local-chat",
    "edgentrag-local-stt",
    "edgentrag-local-embedding",
)


def main() -> None:
    client = boto3.client(
        "sqs",
        region_name=os.getenv("EDGENTRAG_AWS_REGION", "us-east-1"),
        endpoint_url=os.getenv("EDGENTRAG_AWS_ENDPOINT_URL", "http://floci:4566"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
    )
    for name in QUEUE_NAMES:
        result = client.create_queue(QueueName=name)
        print(f"SQS queue ready: {result['QueueUrl']}")


if __name__ == "__main__":
    main()
