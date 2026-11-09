# syntax=docker/dockerfile:1
FROM ghcr.io/datadog/dd-trace-py/testrunner:1d654f8797b4e9e7e42bac859d5cb3484914081b
USER 0
RUN apt-get update \
    && apt-get install -y --no-install-recommends libclang-dev \
    && rm -rf /var/lib/apt/lists/*
USER 1000
