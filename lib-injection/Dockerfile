# This image provides the files needed to install the ddtrace Python package
# and auto instrument Python applications in containerized environments.
FROM python:3.10
WORKDIR /build
ARG DDTRACE_PYTHON_VERSION
RUN python3 -m pip install -U pip==23.0.1
RUN python3 -m pip install packaging==23.0
RUN mkdir -p pkgs
ADD ./dl_wheels.py .
# Note that we only get Python >= 3.7. This is to keep the size of the image
# as small as possible.
RUN python3 dl_wheels.py \
        --python-version=3.12 \
        --python-version=3.11 \
        --python-version=3.10 \
        --python-version=3.9 \
        --python-version=3.8 \
        --python-version=3.7 \
        --ddtrace-version=${DDTRACE_PYTHON_VERSION} \
        --arch x86_64 \
        --arch aarch64 \
        --platform musllinux_1_1 \
        --platform manylinux2014 \
        --output-dir /build/pkgs \
        --verbose

FROM alpine:3.20
ARG UID=10000
RUN addgroup -g 10000 -S datadog && \
    adduser -u ${UID} -S datadog -G datadog
USER ${UID}
WORKDIR /datadog-init

ADD --chown="datadog:datadog" telemetry-forwarder.sh \
    min_compatible_versions.csv \
    sitecustomize.py \
    copy-lib.sh \
    /datadog-init/
COPY --from=0 --chmod=0755 --chown="datadog:datadog" /build/pkgs /datadog-init/ddtrace_pkgs