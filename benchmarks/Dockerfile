FROM debian:bookworm-slim@sha256:abd67ffcfa541b485a3dff59865ab629aa048a6c613e639d36e7456b0b229241 AS base

# Keep this version in sync with the one we actually use in benchmarking-platform
# https://github.com/DataDog/benchmarking-platform/blob/dd-trace-py/container/install-base-deps.sh
ARG PYTHON_VERSION=3.12.10
ARG PYENV_VERSION=2.5.5
RUN apt-get update && apt-get install --no-install-recommends -y \
    make build-essential libssl-dev zlib1g-dev \
    libbz2-dev libreadline-dev libsqlite3-dev wget curl llvm \
    libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev \
    git ca-certificates
ENV PYENV_ROOT "/pyenv"
WORKDIR "$PYENV_ROOT"
ENV PATH "$PYENV_ROOT/shims:$PYENV_ROOT/bin:$PATH"
RUN git clone --depth 1 https://github.com/pyenv/pyenv.git --branch "v$PYENV_VERSION" --single-branch "$PYENV_ROOT"
RUN pyenv install "$PYTHON_VERSION"

FROM debian:bookworm-slim@sha256:abd67ffcfa541b485a3dff59865ab629aa048a6c613e639d36e7456b0b229241
# Keep this version in sync with
# https://github.com/DataDog/benchmarking-platform/blob/dd-trace-py/container/install-base-deps.sh
ARG PYTHON_VERSION=3.12.10

COPY --from=base /pyenv /pyenv
ENV PYENV_ROOT "/pyenv"
ENV PATH "$PYENV_ROOT/shims:$PYENV_ROOT/bin:/root/.cargo/bin/:$PATH"
RUN pyenv global "$PYTHON_VERSION"

# Increase pip/uv timeout and retries to avoid transient network failures
# when downloading packages from PyPI in CI. Set before `pip install -U pip`
# so the upgrade itself benefits from the increased timeout/retries.
ENV PIP_DEFAULT_TIMEOUT=120
ENV PIP_RETRIES=10
ENV UV_HTTP_TIMEOUT=120
ENV UV_HTTP_RETRIES=10

RUN pip install -U pip

ARG SCENARIO=base

ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1

WORKDIR /app

# Install required system dependencies
RUN apt-get update && apt-get install --no-install-recommends -y \
  curl \
  git \
  ca-certificates \
  # ddtrace includes c extensions
  build-essential \
  # libdatadog requires autoconf/automake/libtool to build
  autoconf \
  automake \
  libtool \
  # uuid is used to generate identifier for run if one is not provided
  uuid-runtime \
  # provides ab for testing
  apache2-utils \
  # provides sqlite3 for sql injection tests
  sqlite3 \
  # cleaning up unused files
  && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
  && rm -rf /var/lib/apt/lists/*

# Install Rust toolchain
# TODO: Pin to a specific rustup-init version with SHA256 verification.
# See https://static.rust-lang.org/rustup/ for versioned releases and checksums.
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | \
    sh -s -- --default-toolchain stable -y

# Add base common files used by all scenarios
COPY ./base/ /app/

# Add bm package for scenario framework
COPY ./bm/ /app/bm/

# Add scenario code, overriding anything from base
COPY ./${SCENARIO}/ /app/

ENV SCENARIO=${SCENARIO}
ENV PROFILE_BENCHMARKS=0

ENTRYPOINT ["/app/entrypoint"]
CMD ["/app/benchmark"]
