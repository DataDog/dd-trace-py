#!/usr/bin/env bash

set -ex

if [ -z "$PYENV_ROOT" ]; then
   echo "FAIL! Make sure PYENV_ROOT is set!"
   exit 1
fi

apt-get update && apt-get install --no-install-recommends -y \
                    wget curl ca-certificates git hwinfo jq procps \
	                  software-properties-common build-essential libnss3-dev zlib1g-dev \
	                  libgdbm-dev libncurses5-dev libssl-dev libffi-dev libreadline-dev libsqlite3-dev libbz2-dev \
                  liblzma-dev \
    && apt-get -y clean \
    && rm -rf /var/lib/apt/lists/*

# Install newer pyenv version (must be new enough for Python 3.14.x definitions)
rm -rf "$PYENV_ROOT"
git clone --depth 1 https://github.com/pyenv/pyenv.git --branch "v2.7.3" --single-branch "$PYENV_ROOT"
eval "$(pyenv init -)"
pyenv install 3.9.15   # Required for BP tooling (bp-runner, upload scripts, etc.)
pyenv install 3.12.10  # Available for repos that need it (e.g. dd-trace-py microbenchmarks)
pyenv install 3.14.4  # Available for repos that need it (e.g. dd-trace-py microbenchmarks)
pyenv global 3.9.15    # Keep 3.9 as default; repos select other versions via PYENV_VERSION
pip3 install awscli virtualenv setuptools
# Also install awscli under the non-default versions so the `aws` pyenv shim
# works when PYENV_VERSION is set (e.g. dd-trace-py microbenchmarks).
pyenv shell 3.12.10 && pip3 install awscli && pyenv shell --unset
pyenv shell 3.14.4 && pip3 install awscli && pyenv shell --unset

echo "Install Rust toolchain"

curl https://sh.rustup.rs -sSf | \
  sh -s -- --default-toolchain stable -y

echo "Copying bp-install from benchmarking-platform-tools repo..."

if [ ! -d "/tmp/benchmarking-platform-tools" ]; then
  if [ -n "$CI_JOB_TOKEN" ]; then
    git clone https://gitlab-ci-token:${CI_JOB_TOKEN}@gitlab.ddbuild.io/DataDog/benchmarking-platform-tools /tmp/benchmarking-platform-tools
  else
    mkdir -p ~/.ssh && ssh-keyscan -t rsa github.com >> ~/.ssh/known_hosts
    git clone git@github.com:DataDog/benchmarking-platform-tools /tmp/benchmarking-platform-tools
  fi
fi

mkdir -p /app
cp -r /tmp/benchmarking-platform-tools/images/templates/linux/bp-install /app/bp-install

echo "Success!"
