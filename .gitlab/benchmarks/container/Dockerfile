# The intermediate base layer is built from this repository's own
# benchmarks/Dockerfile by the benchmarking-platform `dd-trace-py` branch
# pipeline and published as `cache-dd-trace-py`. Pinned by digest so the
# in-repo build-benchmark-ci-images job is reproducible and does not move when
# the platform pipeline pushes that tag again. The platform pipeline passes
# this build-arg explicitly; the default is what the in-repo build uses.
ARG INTERMEDIATE_CI_IMAGE=registry.ddbuild.io/ci/benchmarking-platform:cache-dd-trace-py@sha256:cb8a003489e7054482708df65689bb63f4153d3a74df8296a80ca2e50093b0d7
FROM $INTERMEDIATE_CI_IMAGE

SHELL ["/bin/bash", "-c"]

ARG CI_JOB_TOKEN

ENV PATH "/root/.cargo/bin:$PYENV_ROOT/shims:$PYENV_ROOT/bin:/app:/app/bp-runner/:/app/benchmark-github-tools/:/app/benchmark-analyzer/:$PATH"

COPY ./install-base-deps.sh ./install-base-deps.sh
RUN --mount=type=ssh source ./install-base-deps.sh

RUN --mount=type=ssh bp-install benchmark-analyzer bp-runner datadog-agent github-tools
