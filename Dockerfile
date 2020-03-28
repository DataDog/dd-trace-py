# DEV: Use `debian:slim` instead of an `alpine` image to support installing wheels from PyPI
#      this drastically improves test execution time since python dependencies don't all
#      have to be built from source all the time (grpcio takes forever to install)
FROM debian:stretch-slim

# http://bugs.python.org/issue19846
# > At the moment, setting "LANG=C" on a Linux system *fundamentally breaks Python 3*, and that's not OK.
ENV LANG C.UTF-8

RUN \
  # Install system dependencies
  apt-get update \
  && apt-get install -y --no-install-recommends \
      build-essential \
      ca-certificates \
      curl \
      git \
      jq \
      libbz2-dev \
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
      # Needed to support Python 3.4
      libssl1.0-dev \
      # Can be re-enabled once we drop 3.4
      # libssh-dev \
      # libssl-dev \
      patch \
      python-openssl\
      wget \
      zlib1g-dev \
  # Cleaning up apt cache space
  && rm -rf /var/lib/apt/lists/*


# Configure PATH environment for pyenv
ENV PYENV_ROOT /root/.pyenv
ENV PATH ${PYENV_ROOT}/shims:${PYENV_ROOT}/bin:$PATH

# Install pyenv
RUN git clone git://github.com/yyuu/pyenv.git "${PYENV_ROOT}"


# Install all required python versions
RUN \
  pyenv install 2.7.17 \
  && pyenv install 3.4.9 \
  && pyenv install 3.5.9 \
  && pyenv install 3.6.9 \
  && pyenv install 3.7.6 \
  && pyenv install 3.8.1 \
  && pyenv install 3.9-dev \
  && pyenv global 2.7.17 3.4.9 3.5.9 3.6.9 3.7.6 3.8.1 3.9-dev \
  && pip install --upgrade pip

# Install Python dependencies
# DEV: `tox==3.7` introduced parallel execution mode
#      https://tox.readthedocs.io/en/3.7.0/example/basic.html#parallel-mode
RUN pip install "tox>=3.7,<4.0"

CMD ["/bin/bash"]
