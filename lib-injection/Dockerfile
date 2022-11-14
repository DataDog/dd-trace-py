# This image provides the files needed to install the ddtrace Python package
# and auto instrument Python applications in containerized environments.
FROM busybox

ARG UID=10000
RUN addgroup -g 10000 -S datadog && \
    adduser -u ${UID} -S datadog -G datadog
USER ${UID}
WORKDIR /datadog-init
ADD sitecustomize.py /datadog-init/sitecustomize.py
ADD copy-lib.sh /datadog-init/copy-lib.sh
