"""Create the four SQS queues and their dead-letter queues. Idempotent.

Run once, from anywhere that has AWS credentials — your laptop or the server:

    python -m scripts.bootstrap

The S3 bucket already exists from version 1; this does not touch it. Tables are
created by the application itself on start-up.

The attributes below are the interesting part, and worth reading as
documentation of what a queue actually needs.
"""
import json
import logging
import secrets
import sys

from backend.shared import queues
from backend.shared.config import get_settings

logging.basicConfig(level="INFO", format="%(levelname)-7s %(message)s")
log = logging.getLogger("bootstrap")
settings = get_settings()

# How many times a message is retried before it is set aside. Low enough that a
# poison message stops wasting a worker quickly; high enough to ride out a
# service restarting.
#
# Read from settings rather than written twice: the broker needs the same
# number to recognise a last attempt, and two literals would drift apart.
MAX_RECEIVES = settings.queue_max_receives


def ensure_queue(name: str) -> str:
    """Create a queue and its dead-letter queue, and wire them together.

    The dead-letter queue is the point. Without one, a message that can never be
    processed is retried for ever, occupying a worker each time. With one it is
    set aside after a few attempts and becomes a number you can alarm on.
    """
    dlq = queues.client.create_queue(QueueName=f"{name}-dlq")["QueueUrl"]
    dlq_arn = queues.client.get_queue_attributes(
        QueueUrl=dlq, AttributeNames=["QueueArn"]
    )["Attributes"]["QueueArn"]

    main = queues.client.create_queue(
        QueueName=name,
        Attributes={
            # At least as long as the slowest job, or the message is handed to a
            # second worker while the first is still on it. The workers also
            # extend this while they run.
            "VisibilityTimeout": str(settings.queue_visibility_seconds),
            # Long polling: a receive waits for work instead of returning empty.
            # Faster to react and far cheaper.
            "ReceiveMessageWaitTimeSeconds": str(settings.queue_wait_seconds),
            "MessageRetentionPeriod": str(4 * 24 * 60 * 60),      # four days
            "RedrivePolicy": json.dumps({
                "deadLetterTargetArn": dlq_arn,
                "maxReceiveCount": MAX_RECEIVES,
            }),
        },
    )["QueueUrl"]

    log.info("%-22s %s", name, main)
    log.info("%-22s %s  (after %d attempts)", f"{name}-dlq", dlq, MAX_RECEIVES)
    return main


def main() -> int:
    log.info("region: %s", settings.aws_region)

    # Four queues, in two pairs.
    #
    #   ingest, chat   drained by our own workers, inside the compose network
    #   stt, embed     drained by the GPU, through the broker -- it never sees
    #                  these URLs, only the broker does
    urls = {name: ensure_queue(f"edgentrag-{name}")
            for name in ("ingest", "chat", "stt", "embed")}

    print("\nPut these in .env:\n")
    for name, url in urls.items():
        print(f"{name.upper()}_QUEUE_URL={url}")

    if not settings.broker_token:
        print(f"\n# The GPU's token. It grants \"ask for a job\" and nothing else.")
        print(f"BROKER_TOKEN={secrets.token_hex(32)}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
