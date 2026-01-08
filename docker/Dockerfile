# DEV: Use `debian:slim` instead of an `alpine` image to support installing wheels from PyPI
#      this drastically improves test execution time since python dependencies don't all
#      have to be built from source all the time (grpcio takes forever to install)
# Note: Using trixie for GLIBC 2.38+ support (required for Python 3.14 native extensions)
FROM debian:trixie-slim

ARG TARGETARCH
ARG HATCH_VERSION=1.14.2
ARG RIOT_VERSION=0.20.1
ARG UV_VERSION=0.9.0

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
    && echo "deb [arch=amd64,arm64] https://mirror.mariadb.org/repo/11.rolling/debian/ trixie main" > /etc/apt/sources.list.d/mariadb.list \
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
      libjemalloc2 \
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
      sccache \
      unixodbc-dev \
      wget \
      zlib1g-dev \
      awscli \
    # Install azure-functions-core-tools-4, only supported on amd64 architecture for Linux
    # https://github.com/Azure/azure-functions-core-tools/issues/3112
    # Note: Using bookworm repo as trixie is not yet available
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

RUN apt-get update && apt-get install -y clang

ENV PYTHONUNBUFFERED=1
ENV PYENV_ROOT=/home/bits/.pyenv
ENV CARGO_ROOT=/home/bits/.cargo
ENV PATH=/home/bits/.local/bin:${PYENV_ROOT}/shims:${PYENV_ROOT}/bin:${CARGO_ROOT}/bin:$PATH
ENV PYTHON_CONFIGURE_OPTS=--enable-shared
ENV NPM_CONFIG_PREFIX=/home/bits/.local/

RUN apt-get install -y \
  build-essential \
  libssl-dev \
  zlib1g-dev \
  libbz2-dev \
  libreadline-dev \
  libsqlite3-dev \
  libncursesw5-dev \
  liblzma-dev \
  libffi-dev \
  uuid-dev

# ENV PYVER=3.12.12
ENV PYVER=3.10.19

RUN cd /tmp && \
    mkdir -p python-build && \
    cd python-build && \
    curl -O https://www.python.org/ftp/python/$PYVER/Python-$PYVER.tgz && \
    tar xf Python-$PYVER.tgz && \
    cd Python-$PYVER

ENV PREFIX=/opt/python/$PYVER
RUN mkdir -p "$PREFIX"

ENV ASAN_OPTIONS="detect_leaks=0"

####################### START OF ASAN HERE #######################

RUN cd /tmp/python-build/Python-$PYVER && \
  ./configure \
  CFLAGS="-DNDEBUG -fsanitize=address -O1 -fno-omit-frame-pointer" \
  CPPFLAGS="-DNDEBUG -fsanitize=address -O1 -fno-omit-frame-pointer" \
  LDFLAGS="-fsanitize=address" \
  --prefix="$PREFIX" \
  --enable-shared \
  --with-ensurepip=install

####################### START OF UBSAN HERE #######################

######### ENV UBSAN_FLAGS="\
######### "
######### -fsanitize=integer \
######### -fsanitize=shift \
######### -fsanitize=bool \
######### -fsanitize=bounds \
######### -fsanitize=enum \
######### -fsanitize=null \
######### -fsanitize=alignment \
######### -fsanitize=object-size \
######### -fsanitize=vptr \
######### -fsanitize=function \
######### -fsanitize=float-divide-by-zero \
######### -fsanitize=float-cast-overflow \
######### -fsanitize=pointer-overflow \
######### -fsanitize=return \
######### -fsanitize=unreachable \
######### -fsanitize-recover=none \
######### -fno-omit-frame-pointer \
######### -fno-optimize-sibling-calls"

# RUN cd /tmp/python-build/Python-$PYVER && \
#   ./configure \
#   CFLAGS="-DNDEBUG -fsanitize=undefined -g -O1 -fno-omit-frame-pointer $UBSAN_FLAGS" \
#   CPPFLAGS="-DNDEBUG -fsanitize=undefined -g -O1 -fno-omit-frame-pointer $UBSAN_FLAGS" \
#   LDFLAGS="-fsanitize=undefined" \
#   --prefix="$PREFIX" \
#   --enable-shared \
#   --with-ensurepip=install || (cat config.log && exit 1)

####################### START OF MSAN HERE #######################

# RUN cd /tmp/python-build/Python-$PYVER && \
#   ./configure \
#   CC=clang \
#   CXX=clang++ \
#   CFLAGS="-DNDEBUG -O1 -fsanitize=memory -fsanitize-memory-track-origins=2 -fno-omit-frame-pointer -g -O1 -fno-optimize-sibling-calls" \
#   CPPFLAGS="-DNDEBUG -O1 -fsanitize=memory -fsanitize-memory-track-origins=2 -fno-omit-frame-pointer -g -O1 -fno-optimize-sibling-calls" \
#   LDFLAGS="-fsanitize=memory" \
#   --prefix="$PREFIX" \
#   --enable-shared \
#   --with-ensurepip=install

####################### END OF SANITIZERS HERE #######################

RUN cd /tmp/python-build/Python-$PYVER && \
    make -j"$(nproc)"

RUN cd /tmp/python-build/Python-$PYVER && \
    make altinstall

RUN echo "$PREFIX/lib" | tee /etc/ld.so.conf.d/python$PYVER$SUFFIX.conf
RUN ldconfig

RUN echo 'export PATH="$PREFIX/bin:$PATH"' >> ~/.bashrc
RUN echo 'export PATH="$PREFIX/bin:$PATH"' >> ~/.zshrc

# ENV PYVERSHORT=3.12
ENV PYVERSHORT=3.10 

RUN $PREFIX/bin/python$PYVERSHORT -V
RUN $PREFIX/bin/python$PYVERSHORT -c "import ssl,sqlite3,readline,bz2,lzma,uuid; print('OK')"
RUN $PREFIX/bin/python$PYVERSHORT -m pip install --upgrade pip wheel setuptools

USER bits
WORKDIR /home/bits/
 
# Install Rust toolchain
RUN curl https://sh.rustup.rs -sSf | \
    sh -s -- --default-toolchain stable -y

# Use .python-version to specify all Python versions for testing
# COPY .python-version /home/bits/

# Allow running datadog-ci in CI with npx
# RUN npm install -g @datadog/datadog-ci

# Install pyenv and necessary Python versions
#RUN git clone --depth 1 --branch "v2.6.9" https://github.com/pyenv/pyenv "${PYENV_ROOT}" \
#  && pyenv local | xargs -L 1 pyenv install \
#  && cd -

ENV PYTHON_EXEC=$PREFIX/bin/python$PYVERSHORT

RUN $PYTHON_EXEC -m pip install --no-cache-dir -U "riot==${RIOT_VERSION}" "hatch==${HATCH_VERSION}" "uv==${UV_VERSION}"

ENV PATH=$PREFIX/bin:$PATH


ENV ASAN_OPTIONS="\
detect_leaks=0:\
detect_stack_use_after_return=1:\
detect_invalid_pointer_pairs=2:\
strict_init_order=1:\
strict_string_checks=1:\
alloc_dealloc_mismatch=1:\
check_malloc_usable_size=1:\
detect_container_overflow=1:\
detect_odr_violation=2:\
halt_on_error=0:\
abort_on_error=0:\
fast_unwind_on_malloc=0:\
malloc_context_size=50:\
redzone=2048:\
max_allocation_size_mb=4096"

ENV UBSAN_OPTIONS="\
halt_on_error=1:\
abort_on_error=1:\
print_stacktrace=1:\
verbosity=1:\
report_error_type=1:\
"

CMD ["/bin/bash"]
