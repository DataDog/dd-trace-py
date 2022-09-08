#!/usr/bin/env bash
set -ex

export PYTHON_VERSION=3.9.6
export PYENV_VERSION=2.0.4
export PYENV_ROOT="/pyenv"

apt-get update
apt-get install --no-install-recommends -y \
  parallel make build-essential libssl-dev zlib1g-dev \
  libbz2-dev libreadline-dev libsqlite3-dev wget curl llvm \
  libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev \
  git ca-certificates uuid-runtime apache2-utils gfortran cmake unzip hwinfo procps \
  postgresql postgresql-contrib

mkdir -p $PYENV_ROOT
chmod 777 $PYENV_ROOT

if [[ ! -d "$PYENV_ROOT/bin" ]]
then
  git clone --depth 1 https://github.com/pyenv/pyenv.git --branch "v$PYENV_VERSION" --single-branch "$PYENV_ROOT"

  export PATH="$PYENV_ROOT/shims:$PYENV_ROOT/bin:$PATH"
  eval "$(pyenv init -)"

  pyenv install "$PYTHON_VERSION"
else
  export PATH="$PYENV_ROOT/shims:$PYENV_ROOT/bin:$PATH"
  eval "$(pyenv init -)"
fi

pyenv global "$PYTHON_VERSION"