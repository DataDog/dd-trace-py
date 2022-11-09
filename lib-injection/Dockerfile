# This image provides the files needed to install the ddtrace Python package
# and auto instrument Python applications in containerized environments.
FROM busybox

RUN addgroup -g 1000 -S datadog && \
    adduser -u 1000 -S datadog -G datadog
USER datadog
WORKDIR /datadog-init
ADD sitecustomize.py /datadog-init/sitecustomize.py
ADD copy-lib.sh /datadog-init/copy-lib.sh
