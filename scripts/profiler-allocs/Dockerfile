# Dockerfile for analyzing native allocations made by the dd-trace-py profiler.
# Builds dd-trace-py with profiling extensions and installs ddprof for native
# heap profiling.
#
# Build:
#   docker build -t ddtrace-profiler-allocs -f scripts/profiler-allocs/Dockerfile .
#
# Run:
#   docker run --rm -v $(pwd)/artifacts:/artifacts ddtrace-profiler-allocs

FROM debian:trixie-slim

ARG TARGETARCH
ARG DDPROF_VERSION=0.25.0

ENV LANG=C.UTF-8
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies (mirrors profiling_native CI image)
# autoconf/automake/libtool needed for libdatadog's libunwind build
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      autoconf \
      automake \
      build-essential \
      ca-certificates \
      cmake \
      curl \
      git \
      libbz2-dev \
      libffi-dev \
      liblzma-dev \
      libncurses5-dev \
      libncursesw5-dev \
      libreadline-dev \
      libsqlite3-dev \
      libssl-dev \
      libtool \
      make \
      patchelf \
      pkg-config \
      wget \
      xz-utils \
      zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Install pyenv + Python 3.12 with --enable-shared (needed for native extensions)
ENV PYENV_ROOT=/root/.pyenv
ENV PATH="${PYENV_ROOT}/shims:${PYENV_ROOT}/bin:${PATH}"
ENV PYTHON_CONFIGURE_OPTS="--enable-shared"

RUN git clone --depth 1 --branch "v2.6.17" https://github.com/pyenv/pyenv "${PYENV_ROOT}" \
    && pyenv install 3.12 \
    && pyenv global 3.12 \
    && echo "${PYENV_ROOT}/versions/3.12.12/lib" >> /etc/ld.so.conf.d/pyenv.conf \
    && ldconfig

# Install Rust
ENV CARGO_HOME=/root/.cargo
ENV PATH="${CARGO_HOME}/bin:${PATH}"
RUN curl https://sh.rustup.rs -sSf | sh -s -- --profile minimal --default-toolchain stable -y

# Install Python build tools
RUN pip install --no-cache-dir -U pip setuptools wheel \
    && pip install --no-cache-dir cmake setuptools_rust cython patchelf pybind11

# Download ddprof
RUN ARCH=$(dpkg --print-architecture) && \
    curl -L -o /tmp/ddprof.tar.xz \
      "https://github.com/DataDog/ddprof/releases/download/v${DDPROF_VERSION}/ddprof-${DDPROF_VERSION}-${ARCH}-linux.tar.xz" && \
    mkdir -p /opt/ddprof && \
    tar -xf /tmp/ddprof.tar.xz -C /opt/ddprof --strip-components=1 && \
    rm /tmp/ddprof.tar.xz
ENV PATH="/opt/ddprof/bin:${PATH}"

# Copy dd-trace-py source and build with profiling extensions
WORKDIR /dd-trace-py
COPY . .

# Override .python-version — we only have 3.12 installed
RUN echo "3.12" > .python-version

# DD_FAST_BUILD=1 disables LTO + Abseil for faster builds
RUN DD_FAST_BUILD=1 pip install --no-cache-dir -e . -v

# Copy test app
COPY scripts/profiler-allocs/test_app.py /app/test_app.py

WORKDIR /app

# Run test app under ddprof with cpu_live_heap preset.
# --debug_pprof_prefix saves pprof files locally (as .pprof.zst).
# -u 10 sets a short upload period so we get multiple profiles.
CMD ["ddprof", \
     "-l", "notice", \
     "--preset", "cpu_live_heap", \
     "--debug_pprof_prefix", "/artifacts/ddprof", \
     "-u", "10", \
     "--", \
     "python3", "/app/test_app.py"]
