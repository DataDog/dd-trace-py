FROM python:3.11-slim

ARG DDTRACE_SPEC=ddtrace[appsec]

WORKDIR /app

RUN python -m pip install --upgrade pip \
    && python -m pip install --no-cache-dir "${DDTRACE_SPEC}" flask

COPY app.py /app/app.py

ENV DD_IAST_ENABLED=true
ENV DD_IAST_REQUEST_SAMPLING=100
ENV DD_APPSEC_ENABLED=true
ENV DD_TRACE_ENABLED=true
ENV DD_REMOTE_CONFIGURATION_ENABLED=false
ENV PORT=8000

EXPOSE 8000

CMD ["ddtrace-run", "python", "/app/app.py"]
