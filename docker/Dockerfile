# DEV: Use `debian:slim` instead of an `alpine` image to support installing wheels from PyPI
#      this drastically improves test execution time since python dependencies don't all
#      have to be built from source all the time (grpcio takes forever to install)
FROM debian:buster-20221219-slim

ARG TARGETARCH
ARG HATCH_VERSION=1.12.0

# http://bugs.python.org/issue19846
# > At the moment, setting "LANG=C" on a Linux system *fundamentally breaks Python 3*, and that's not OK.
ENV LANG C.UTF-8

# https://support.circleci.com/hc/en-us/articles/360045268074-Build-Fails-with-Too-long-with-no-output-exceeded-10m0s-context-deadline-exceeded-
ENV PYTHONUNBUFFERED=1
# Configure PATH environment for pyenv
ENV PYENV_ROOT=/root/.pyenv
ENV CARGO_ROOT=/root/.cargo
ENV PATH=${PYENV_ROOT}/shims:${PYENV_ROOT}/bin:${CARGO_ROOT}/bin:$PATH
ENV PYTHON_CONFIGURE_OPTS=--enable-shared

# Use .python-version to specify all Python versions for testing
COPY .python-version /root/

RUN \
  # Install system dependencies
  apt-get update \
  && apt-get install -y --no-install-recommends \
      apt-transport-https \
      build-essential \
      ca-certificates \
      clang-format \
      curl \
      gdb \
      git \
      gnupg \
      jq \
      libbz2-dev \
      libenchant-dev \
      libffi-dev \
      liblzma-dev \
      libmemcached-dev \
      libmemcached-dev \
      libncurses5-dev \
      libncursesw5-dev \
      libpq-dev \
      libreadline-dev \
      libsasl2-dev \
      libsqlite3-dev \
      libsqliteodbc \
      libssh-dev \
      libssl-dev \
      patch \
      python-openssl\
      unixodbc-dev \
      wget \
      zlib1g-dev \
  # Install Mariadb
  && wget https://downloads.mariadb.com/MariaDB/mariadb_repo_setup \
  && chmod +x mariadb_repo_setup \
  && ./mariadb_repo_setup \
  && apt-get install -y --no-install-recommends libmariadb-dev libmariadb-dev-compat \
  && rm mariadb_repo_setup \
  # For running datadog-ci CLI with npx
  && apt-get install -y --no-install-recommends nodejs npm \
  # Cleaning up apt cache space
  && rm -rf /var/lib/apt/lists/*

# Install Rust toolchain
RUN curl https://sh.rustup.rs -sSf | \
    sh -s -- --default-toolchain stable -y

# Install pyenv and necessary Python versions
RUN git clone --depth 1 --branch v2.4.2 https://github.com/pyenv/pyenv "${PYENV_ROOT}" \
  && cd /root \
  && pyenv local | xargs -L 1 pyenv install \
  && cd -

RUN if [ "$TARGETARCH" = "amd64" ]; \
    then curl -L https://github.com/pypa/hatch/releases/download/hatch-v${HATCH_VERSION}/hatch-x86_64-unknown-linux-gnu.tar.gz | tar zx; \
    else curl -L https://github.com/pypa/hatch/releases/download/hatch-v${HATCH_VERSION}/hatch-aarch64-unknown-linux-gnu.tar.gz | tar zx; \
    fi \
  && install -t /usr/local/bin hatch \
  && hatch -q

CMD ["/bin/bash"]
