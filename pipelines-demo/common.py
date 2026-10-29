"""Shared helpers for the pipelines-demo apps and setup scripts.

Resolves the demo broker endpoints (the docker-compose services) from the
environment, so every app runs with plain
``DD_SERVICE=... DD_ENV=demo ddtrace-run python apps/<app>.py`` from the
pipelines-demo directory. The tap (tap.py) replaces the real agent, so apps
also export ``DD_TRACE_AGENT_URL=http://localhost:8126``.
"""

import os


DEMO_ROOT = os.path.dirname(os.path.abspath(__file__))

KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")
AWS_ENDPOINT = os.environ.get("AWS_ENDPOINT", "http://localhost:4566")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
PUBSUB_PROJECT = os.environ.get("PUBSUB_PROJECT", "demo-project")
RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")

# google-cloud-pubsub reads this at client construction time and redirects to
# the emulator; set it here so setup scripts and apps work even when the caller
# forgot to export it.
os.environ.setdefault("PUBSUB_EMULATOR_HOST", "localhost:8085")


def boto3_client(service):
    """A boto3 client for ``service`` pointed at the demo LocalStack.

    boto3 is imported lazily so Kafka/PubSub/RabbitMQ apps do not need it
    installed.
    """
    import boto3

    return boto3.client(
        service,
        endpoint_url=AWS_ENDPOINT,
        region_name=AWS_REGION,
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
