# DEV: Use `debian:slim` instead of an `alpine` image to support installing wheels from PyPI
#      this drastically improves test execution time since python dependencies don't all
#      have to be built from source all the time (grpcio takes forever to install)
FROM debian:bookworm-slim

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

# Install system dependencies
RUN apt-get update \
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
      patch \
      unixodbc-dev \
      wget \
      zlib1g-dev \
      awscli

# Allow running datadog-ci in CI with npx
RUN apt-get install -y --no-install-recommends nodejs npm \
  && npm install -g @datadog/datadog-ci

# MariaDB is a dependency for tests
RUN curl https://mariadb.org/mariadb_release_signing_key.pgp | gpg --dearmor > /etc/apt/trusted.gpg.d/mariadb.gpg \
  && echo "deb [arch=amd64,arm64] https://mirror.mariadb.org/repo/11.rolling/debian/ bookworm main" > /etc/apt/sources.list.d/mariadb.list \
  && apt-get update \
  && apt-get install -y --no-install-recommends libmariadb-dev libmariadb-dev-compat

# Install azure-functions-core-tools-4, only supported on amd64 architecture for Linux
# https://github.com/Azure/azure-functions-core-tools/issues/3112
RUN if [ "$TARGETARCH" = "amd64" ]; \
  then \
    curl https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > microsoft.gpg \
    && mv microsoft.gpg /etc/apt/trusted.gpg.d/microsoft.gpg \
    && echo "deb [arch=amd64] https://packages.microsoft.com/repos/microsoft-debian-bookworm-prod bookworm main" > /etc/apt/sources.list.d/dotnetdev.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends azure-functions-core-tools-4=4.0.6280-1; \
  fi

# Google Chrome is needed for selenium contrib tests but is currently only available on amd64
RUN if [ "$TARGETARCH" = "amd64" ]; \
  then \
    curl https://dl.google.com/linux/linux_signing_key.pub |gpg --dearmor > /etc/apt/trusted.gpg.d/google.gpg \
    && echo 'deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main' > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-chrome-stable ; \
  fi

# Cleaning up apt cache space
RUN rm -rf /var/lib/apt/lists/*

# Install Rust toolchain
RUN curl https://sh.rustup.rs -sSf | \
    sh -s -- --default-toolchain stable -y

# Install pyenv and necessary Python versions
RUN git clone --depth 1 --branch v2.4.22 https://github.com/pyenv/pyenv "${PYENV_ROOT}" \
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
