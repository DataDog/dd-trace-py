# DEV: Use `debian:slim` instead of an `alpine` image to support installing wheels from PyPI
#      this drastically improves test execution time since python dependencies don't all
#      have to be built from source all the time (grpcio takes forever to install)
FROM debian:bookworm-slim

ARG TARGETARCH
ARG HATCH_VERSION=1.14.1
ARG RIOT_VERSION=0.20.1
ARG UV_VERSION=0.8.2

# http://bugs.python.org/issue19846
# > At the moment, setting "LANG=C" on a Linux system *fundamentally breaks Python 3*, and that's not OK.
ENV LANG=C.UTF-8

# Use root user to install system dependencies
USER root

# Create our bits user
RUN useradd -ms /bin/bash bits

# Install system dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && curl https://mariadb.org/mariadb_release_signing_key.pgp | gpg --dearmor > /etc/apt/trusted.gpg.d/mariadb.gpg \
    && echo "deb [arch=amd64,arm64] https://mirror.mariadb.org/repo/11.rolling/debian/ bookworm main" > /etc/apt/sources.list.d/mariadb.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
      apt-transport-https \
      build-essential \
      clang-format \
      gdb \
      git \
      jq \
      libbz2-dev \
      libenchant-2-dev \
      libffi-dev \
      liblzma-dev \
      libmariadb-dev \
      libmariadb-dev-compat \
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
      nodejs \
      npm \
      patch \
      unixodbc-dev \
      wget \
      zlib1g-dev \
      awscli \
    # Install azure-functions-core-tools-4, only supported on amd64 architecture for Linux
    # https://github.com/Azure/azure-functions-core-tools/issues/3112
    && if [ "$TARGETARCH" = "amd64" ]; \
    then \
      curl https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > microsoft.gpg \
      && mv microsoft.gpg /etc/apt/trusted.gpg.d/microsoft.gpg \
      && echo "deb [arch=amd64] https://packages.microsoft.com/repos/microsoft-debian-bookworm-prod bookworm main" > /etc/apt/sources.list.d/dotnetdev.list \
      && apt-get update \
      && apt-get install -y --no-install-recommends azure-functions-core-tools-4=4.1.0-1; \
    fi \
    # Google Chrome is needed for selenium contrib tests but is currently only available on amd64
    && if [ "$TARGETARCH" = "amd64" ]; \
    then \
      curl https://dl.google.com/linux/linux_signing_key.pub |gpg --dearmor > /etc/apt/trusted.gpg.d/google.gpg \
      && echo 'deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main' > /etc/apt/sources.list.d/google-chrome.list \
      && apt-get update \
      && apt-get install -y --no-install-recommends google-chrome-stable ; \
    fi \
    # Cleaning up apt cache space
    && rm -rf /var/lib/apt/lists/*


USER bits
WORKDIR /home/bits/

ENV PYTHONUNBUFFERED=1
ENV PYENV_ROOT=/home/bits/.pyenv
ENV CARGO_ROOT=/home/bits/.cargo
ENV PATH=/home/bits/.local/bin:${PYENV_ROOT}/shims:${PYENV_ROOT}/bin:${CARGO_ROOT}/bin:$PATH
ENV PYTHON_CONFIGURE_OPTS=--enable-shared
ENV NPM_CONFIG_PREFIX=/home/bits/.local/

# Install Rust toolchain
RUN curl https://sh.rustup.rs -sSf | \
    sh -s -- --default-toolchain stable -y

# Use .python-version to specify all Python versions for testing
COPY .python-version /home/bits/

# Allow running datadog-ci in CI with npx
RUN npm install -g @datadog/datadog-ci

# Install pyenv and necessary Python versions
RUN git clone --depth 1 --branch "v2.6.5" https://github.com/pyenv/pyenv "${PYENV_ROOT}" \
  && pyenv local | xargs -L 1 pyenv install \
  && cd -

RUN pip install --no-cache-dir -U "riot==${RIOT_VERSION}" "hatch==${HATCH_VERSION}" "uv==${UV_VERSION}"

CMD ["/bin/bash"]
