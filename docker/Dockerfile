ARG MANYLINUX_IMAGE_BASE_URL=quay.io/pypa/
ARG MANYLINUX_IMAGE_TAG=2024.08.19-1
ARG MANYLINUX_TAG=manylinux2014
FROM --platform=linux/amd64 ${MANYLINUX_IMAGE_BASE_URL}${MANYLINUX_TAG}_x86_64:${MANYLINUX_IMAGE_TAG} as base-amd64
FROM --platform=linux/amd64 ${MANYLINUX_IMAGE_BASE_URL}${MANYLINUX_TAG}_x86_64:${MANYLINUX_IMAGE_TAG} as base-386
FROM --platform=linux/amd64 ${MANYLINUX_IMAGE_BASE_URL}${MANYLINUX_TAG}_aarch64:${MANYLINUX_IMAGE_TAG} as base-arm64

FROM base-${BUILDARCH}

ENV PIPX_DEFAULT_PYTHON=python3.12
ENV PYTHONUNBUFFERED=1
ENV CARGO_ROOT=/root/.cargo
ENV PATH=${CARGO_ROOT}/bin:$PATH

# Ensure Python versions we need are installed
RUN manylinux-interpreters ensure cp37-cp37m cp38-cp38 cp39-cp39 cp310-cp310 cp311-cp311 cp312-cp312 cp313-cp313

# Install jq
RUN curl -L https://github.com/jqlang/jq/releases/download/jq-1.7.1/jq-linux-$BUILDARCH -o /usr/local/bin/jq && \
    chmod +x /usr/local/bin/jq

# Install system dependencies
RUN yum install -y epel-release && \
    yum install -y \
      ca-certificates \
      enchant-devel \
      gdb \
      libffi-devel \
      libssh2-devel \
      mariadb-devel \
      memcached-devel \
      ncurses-devel \
      nodejs \
      openssl-devel \
      postgresql-devel \
      readline-devel \
      sqlite-devel \
      wget \
      xz-devel \
      zlib-devel && \
    yum -y clean all && rm -fr /var/cache

# Install Rust toolchain
RUN curl https://sh.rustup.rs -sSf | \
    sh -s -- --default-toolchain stable -y

ARG HATCH_VERSION=1.12.0
# Install Hatch
RUN pipx install hatch==${HATCH_VERSION}

CMD ["/bin/bash"]
