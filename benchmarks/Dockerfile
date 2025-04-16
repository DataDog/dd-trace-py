FROM debian:buster-slim as base

ARG PYTHON_VERSION=3.13
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

FROM debian:buster-slim
ARG PYTHON_VERSION=3.13

COPY --from=base /pyenv /pyenv
ENV PYENV_ROOT "/pyenv"
ENV PATH "$PYENV_ROOT/shims:$PYENV_ROOT/bin:/root/.cargo/bin/:$PATH"
RUN pyenv global "$PYTHON_VERSION"
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
RUN curl https://sh.rustup.rs -sSf | \
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
