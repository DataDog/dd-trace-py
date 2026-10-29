# DEV: Use `debian:slim` instead of an `alpine` image to support installing wheels from PyPI
#      this drastically improves test execution time since python dependencies don't all
#      have to be built from source all the time (grpcio takes forever to install)
# Note: Using trixie for GLIBC 2.38+ support (required for Python 3.14 native extensions)
FROM debian:trixie-slim@sha256:4ffb3a1511099754cddc70eb1b12e50ffdb67619aa0ab6c13fcd800a78ef7c7a

ARG TARGETARCH
ARG UV_VERSION=0.13.5

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && update-ca-certificates -f \
    && rm -rf /var/lib/apt/lists/*

# http://bugs.python.org/issue19846
# > At the moment, setting "LANG=C" on a Linux system *fundamentally breaks Python 3*, and that's not OK.
ENV LANG=C.UTF-8

# Use root user to install system dependencies
USER root

# Create our bits user.
# Also create /var/crash: the CI nodes' kernel.core_pattern writes core dumps there, and
# the kernel silently drops a core when that directory is missing or not writable by the
# crashing process. 1777 (sticky, world-writable) lets a process under any uid write its
# core without letting users delete each other's files.
RUN useradd -ms /bin/bash -u 1000 bits \
    && install -d -m 1777 /var/crash

# Install system dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && curl --proto '=https' --tlsv1.2 -sSf --retry 5 --retry-delay 2 --retry-connrefused https://mariadb.org/mariadb_release_signing_key.pgp -o /tmp/mariadb_key.pgp \
    && gpg --no-default-keyring --keyring /tmp/mariadb_keyring.gpg --import /tmp/mariadb_key.pgp 2>/dev/null \
    && gpg --no-default-keyring --keyring /tmp/mariadb_keyring.gpg --with-colons --fingerprint 2>/dev/null \
       | grep -qi "^fpr:.*:177F4010FE56CA3336300305F1656F24C74CD1D8:$" \
    && gpg --no-default-keyring --keyring /tmp/mariadb_keyring.gpg --export > /etc/apt/trusted.gpg.d/mariadb.gpg \
    && rm -f /tmp/mariadb_key.pgp /tmp/mariadb_keyring.gpg /tmp/mariadb_keyring.gpg~ \
    && echo "deb [arch=amd64,arm64] https://mirror.mariadb.org/repo/11.rolling/debian/ trixie main" > /etc/apt/sources.list.d/mariadb.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
      apt-transport-https \
      autoconf \
      automake \
      build-essential \
      gdb \
      git \
      jq \
      libbz2-dev \
      libenchant-2-dev \
      libffi-dev \
      libjemalloc2 \
      liblzma-dev \
      libmariadb-dev \
      libmariadb-dev-compat \
      libmemcached-dev \
      libmemcached-dev \
      libmpdec-dev \
      libncurses5-dev \
      libncursesw5-dev \
      libtool \
      libpq-dev \
      libreadline-dev \
      libsasl2-dev \
      libsqlite3-dev \
      libsqliteodbc \
      libssh-dev \
      python3.13-dev \
      patch \
      sccache \
      unixodbc-dev \
      wget \
      zlib1g-dev \
    # Install azure-functions-core-tools-4, only supported on amd64 architecture for Linux
    # https://github.com/Azure/azure-functions-core-tools/issues/3112
    # Note: Using bookworm repo as trixie is not yet available
    && if [ "$TARGETARCH" = "amd64" ]; \
    then \
      curl --proto '=https' --tlsv1.2 -sSf --retry 5 --retry-delay 2 --retry-connrefused https://packages.microsoft.com/keys/microsoft.asc -o /tmp/microsoft_key.asc \
      && gpg --no-default-keyring --keyring /tmp/ms_keyring.gpg --import /tmp/microsoft_key.asc 2>/dev/null \
      && gpg --no-default-keyring --keyring /tmp/ms_keyring.gpg --with-colons --fingerprint 2>/dev/null \
         | grep -qi "^fpr:.*:BC528686B50D79E339D3721CEB3E94ADBE1229CF:$" \
      && gpg --no-default-keyring --keyring /tmp/ms_keyring.gpg --export > /etc/apt/trusted.gpg.d/microsoft.gpg \
      && rm -f /tmp/microsoft_key.asc /tmp/ms_keyring.gpg /tmp/ms_keyring.gpg~ \
      && echo "deb [arch=amd64] https://packages.microsoft.com/repos/microsoft-debian-bookworm-prod bookworm main" > /etc/apt/sources.list.d/dotnetdev.list \
      && apt-get update \
      && apt-get install -y --no-install-recommends azure-functions-core-tools-4=4.1.0-1; \
    fi \
    # Cleaning up apt cache space
    && rm -rf /var/lib/apt/lists/*

# Install ddtest before switching to the unprivileged user because /usr/local/bin is root-owned.
ARG DDTEST_VERSION=1.7.0
RUN curl -fsSL --retry 5 --retry-delay 2 --retry-connrefused "https://github.com/DataDog/ddtest/releases/download/v${DDTEST_VERSION}/ddtest-linux-${TARGETARCH}" -o /usr/local/bin/ddtest \
  && chmod +x /usr/local/bin/ddtest

USER 1000
WORKDIR /home/bits/

ENV PYTHONUNBUFFERED=1
ENV PYENV_ROOT=/home/bits/.pyenv
ENV CARGO_ROOT=/home/bits/.cargo
ENV PATH=/home/bits/.local/bin:${PYENV_ROOT}/shims:${PYENV_ROOT}/bin:${CARGO_ROOT}/bin:$PATH
# --enable-shared is needed for editable ddtrace builds and pybind11. Nothing runs CPython's own
# test suite, so skip installing its test modules and the static libpython; the default -g build
# keeps debug symbols in libpython for gdb core backtraces.
ENV PYTHON_CONFIGURE_OPTS="--enable-shared --disable-test-modules --without-static-libpython"

# Install Rust toolchain
# TODO: Pin to a specific rustup-init version with SHA256 verification.
# See https://static.rust-lang.org/rustup/ for versioned releases and checksums.
# The minimal profile plus rustfmt and clippy is the default profile without rust-docs (~900MB).
RUN curl --proto '=https' --tlsv1.2 -sSf --retry 5 --retry-delay 2 --retry-connrefused -o /tmp/rustup-init.sh https://sh.rustup.rs && \
    sh /tmp/rustup-init.sh --profile minimal --default-toolchain stable --component rustfmt,clippy -y && \
    rm /tmp/rustup-init.sh

# Use .python-version to specify all Python versions for testing
COPY .python-version /home/bits/

# ~/.local/bin is on PATH for pip's user-install fallback, and scripts/ddtest chowns ~/.local
# when running as a different uid, so the directory must exist.
RUN mkdir -p /home/bits/.local/bin

# Install pyenv and necessary Python versions.
# Pin pyenv to a release that knows CPython 3.15.0 final (python.org 2026-10-09).
RUN git clone --depth 1 --branch "v2.8.9" https://github.com/pyenv/pyenv "${PYENV_ROOT}" \
  && pyenv local | xargs -L 1 pyenv install \
  # Python < 3.10 ignores --disable-test-modules and --without-static-libpython, so remove the
  # same files from those versions after install.
  && rm -rf "${PYENV_ROOT}"/versions/3.[89].*/lib/python3.[89]/test \
    "${PYENV_ROOT}"/versions/3.[89].*/lib/python3.[89]/config-3.[89]*/libpython3.[89].a \
  && cd -

# Upgrade every pyenv interpreter's certificate bundle, including pip's vendored copy.
# Python 3.8 and 3.9 cannot install pip 26, so copy the fixed certifi bundle there too.
RUN set -eu; \
    for python in "${PYENV_ROOT}"/versions/*/bin/python; do \
      "$python" -m pip install --no-cache-dir -U "certifi>=2026.10.19"; \
      if "$python" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then \
        "$python" -m pip install --no-cache-dir -U "pip>=26.2.2"; \
      fi; \
      cp "$("$python" -c 'import certifi; print(certifi.where())')" \
        "$("$python" -c 'import pathlib, pip; print(pathlib.Path(pip.__file__).parent / "_vendor/certifi/cacert.pem")')"; \
    done

RUN pip install --no-cache-dir -U "uv==${UV_VERSION}"

# Python builds and installers can leave extracted CA bundles in temporary files.
RUN find /tmp -maxdepth 1 -type f -name 'tmp*' -delete

CMD ["/bin/bash"]
