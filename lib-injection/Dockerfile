FROM scratch AS python_agent
COPY d?-trace-py /

# This image provides the files needed to install the ddtrace Python package
# and auto instrument Python applications in containerized environments.
FROM busybox

ARG UID=10000
ARG DDTRACE_PYTHON_VERSION
ENV DDTRACE_PYTHON_VERSION=$DDTRACE_PYTHON_VERSION
RUN addgroup -g 10000 -S datadog && \
    adduser -u ${UID} -S datadog -G datadog
USER ${UID}

WORKDIR /datadog-init
ADD sitecustomize.py /datadog-init/sitecustomize.py
# Use ~ as a delimiter because git urls can contain slashes
RUN sed -i "s~<DD_TRACE_VERSION_TO_BE_REPLACED>~${DDTRACE_PYTHON_VERSION}~g" /datadog-init/sitecustomize.py
#Using wildcard (?) if directory no exists it won't fail
COPY --from=python_agent d?-trace-py /datadog-init/dd-trace-py
ADD copy-lib.sh /datadog-init/copy-lib.sh