"""Shared helpers for the pipelines-demo apps and setup scripts.

Resolves the demo broker endpoints (the docker-compose services) from the
environment, so every app runs with plain
``DD_SERVICE=... DD_ENV=demo ddtrace-run python apps/<app>.py`` from the
pipelines-demo directory. The tap (tap.py) replaces the real agent, so apps
also export ``DD_TRACE_AGENT_URL=http://localhost:8126``.

This demo covers the Python+Kafka slice of DSM2-531: the compose file runs
Kafka (plus Zookeeper) only; the other queuing systems get their own demo
topologies in their own subtasks.
"""

import os


DEMO_ROOT = os.path.dirname(os.path.abspath(__file__))

KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")
