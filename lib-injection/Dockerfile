# This image provides the files needed to install the ddtrace Python package
# and auto instrument Python applications in containerized environments.
FROM python:3.12 as build-312
WORKDIR /build
ARG DDTRACE_PYTHON_VERSION
RUN mkdir -p pkgs
ADD ./build.py .
RUN python3 build.py \
        --ddtrace-version=${DDTRACE_PYTHON_VERSION} \
        --output-dir /build/pkgs/site-packages-ddtrace-py3.12-manylinux2014 \
        --verbose

FROM python:3.11 as build-311
WORKDIR /build
ARG DDTRACE_PYTHON_VERSION
RUN mkdir -p pkgs
ADD ./build.py .
RUN python3 build.py \
        --ddtrace-version=${DDTRACE_PYTHON_VERSION} \
        --output-dir /build/pkgs/site-packages-ddtrace-py3.11-manylinux2014 \
        --verbose

FROM python:3.10 as build-310
WORKDIR /build
ARG DDTRACE_PYTHON_VERSION
RUN mkdir -p pkgs
ADD ./build.py .
RUN python3 build.py \
        --ddtrace-version=${DDTRACE_PYTHON_VERSION} \
        --output-dir /build/pkgs/site-packages-ddtrace-py3.10-manylinux2014 \
        --verbose

FROM python:3.9 as build-39
WORKDIR /build
ARG DDTRACE_PYTHON_VERSION
RUN mkdir -p pkgs
ADD ./build.py .
RUN python3 build.py \
        --ddtrace-version=${DDTRACE_PYTHON_VERSION} \
        --output-dir /build/pkgs/site-packages-ddtrace-py3.9-manylinux2014 \
        --verbose

FROM python:3.8 as build-38
WORKDIR /build
ARG DDTRACE_PYTHON_VERSION
RUN mkdir -p pkgs
ADD ./build.py .
RUN python3 build.py \
        --ddtrace-version=${DDTRACE_PYTHON_VERSION} \
        --output-dir /build/pkgs/site-packages-ddtrace-py3.8-manylinux2014 \
        --verbose

FROM python:3.7 as build-37
WORKDIR /build
ARG DDTRACE_PYTHON_VERSION
RUN mkdir -p pkgs
ADD ./build.py .
RUN python3 build.py \
        --ddtrace-version=${DDTRACE_PYTHON_VERSION} \
        --output-dir /build/pkgs/site-packages-ddtrace-py3.7-manylinux2014 \
        --verbose

FROM python:3.12-alpine as build-312-alpine
WORKDIR /build
ARG DDTRACE_PYTHON_VERSION
RUN apk add gcc python3-dev musl-dev linux-headers
RUN mkdir -p pkgs
ADD ./build.py .
RUN python3 build.py \
        --ddtrace-version=${DDTRACE_PYTHON_VERSION} \
        --output-dir /build/pkgs/site-packages-ddtrace-py3.12-musllinux_1_1 \
        --verbose

FROM python:3.11-alpine as build-311-alpine
WORKDIR /build
ARG DDTRACE_PYTHON_VERSION
RUN apk add gcc python3-dev musl-dev linux-headers
RUN mkdir -p pkgs
ADD ./build.py .
RUN python3 build.py \
        --ddtrace-version=${DDTRACE_PYTHON_VERSION} \
        --output-dir /build/pkgs/site-packages-ddtrace-py3.11-musllinux_1_1 \
        --verbose

FROM python:3.10-alpine as build-310-alpine
WORKDIR /build
ARG DDTRACE_PYTHON_VERSION
RUN apk add gcc python3-dev musl-dev linux-headers
RUN mkdir -p pkgs
ADD ./build.py .
RUN python3 build.py \
        --ddtrace-version=${DDTRACE_PYTHON_VERSION} \
        --output-dir /build/pkgs/site-packages-ddtrace-py3.10-musllinux_1_1 \
        --verbose

FROM python:3.9-alpine as build-39-alpine
WORKDIR /build
ARG DDTRACE_PYTHON_VERSION
RUN apk add gcc python3-dev musl-dev linux-headers
RUN mkdir -p pkgs
ADD ./build.py .
RUN python3 build.py \
        --ddtrace-version=${DDTRACE_PYTHON_VERSION} \
        --output-dir /build/pkgs/site-packages-ddtrace-py3.9-musllinux_1_1 \
        --verbose

FROM python:3.8-alpine as build-38-alpine
WORKDIR /build
ARG DDTRACE_PYTHON_VERSION
RUN apk add gcc python3-dev musl-dev linux-headers
RUN mkdir -p pkgs
ADD ./build.py .
RUN python3 build.py \
        --ddtrace-version=${DDTRACE_PYTHON_VERSION} \
        --output-dir /build/pkgs/site-packages-ddtrace-py3.8-musllinux_1_1 \
        --verbose

FROM python:3.7-alpine as build-37-alpine
WORKDIR /build
ARG DDTRACE_PYTHON_VERSION
RUN apk add gcc python3-dev musl-dev linux-headers
RUN mkdir -p pkgs
ADD ./build.py .
RUN python3 build.py \
        --ddtrace-version=${DDTRACE_PYTHON_VERSION} \
        --output-dir /build/pkgs/site-packages-ddtrace-py3.7-musllinux_1_1 \
        --verbose

FROM alpine:3.18.3
COPY --from=build-312 /build/pkgs /datadog-init/ddtrace_pkgs
COPY --from=build-311 /build/pkgs /datadog-init/ddtrace_pkgs
COPY --from=build-310 /build/pkgs /datadog-init/ddtrace_pkgs
COPY --from=build-39 /build/pkgs /datadog-init/ddtrace_pkgs
COPY --from=build-38 /build/pkgs /datadog-init/ddtrace_pkgs
COPY --from=build-37 /build/pkgs /datadog-init/ddtrace_pkgs
COPY --from=build-312-alpine /build/pkgs /datadog-init/ddtrace_pkgs
COPY --from=build-311-alpine /build/pkgs /datadog-init/ddtrace_pkgs
COPY --from=build-310-alpine /build/pkgs /datadog-init/ddtrace_pkgs
COPY --from=build-39-alpine /build/pkgs /datadog-init/ddtrace_pkgs
COPY --from=build-38-alpine /build/pkgs /datadog-init/ddtrace_pkgs
COPY --from=build-37-alpine /build/pkgs /datadog-init/ddtrace_pkgs
ARG UID=10000
RUN addgroup -g 10000 -S datadog && \
    adduser -u ${UID} -S datadog -G datadog
RUN chown -R datadog:datadog /datadog-init/ddtrace_pkgs
RUN chmod -R a+rw /datadog-init/ddtrace_pkgs
USER ${UID}
WORKDIR /datadog-init
ADD sitecustomize.py /datadog-init/sitecustomize.py
ADD copy-lib.sh /datadog-init/copy-lib.sh
