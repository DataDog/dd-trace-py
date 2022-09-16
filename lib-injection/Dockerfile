# This image provides the files needed to install the ddtrace Python package
# and auto instrument Python applications in containerized environments.
FROM busybox

WORKDIR /datadog-init

ADD sitecustomize.py /datadog-init/sitecustomize.py
ADD copy-lib.sh /datadog-init/copy-lib.sh
