FROM python:3.11-slim

ARG DDTRACE_SPEC=ddtrace

WORKDIR /app

RUN python -m pip install --upgrade pip \
    && python -m pip install --no-cache-dir "${DDTRACE_SPEC}"

COPY repro.py /app/repro.py

CMD ["python", "/app/repro.py"]
