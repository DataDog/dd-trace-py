# DEV: Use `debian:slim` instead of an `alpine` image to support installing wheels from PyPI
#      this drastically improves test execution time since python dependencies don't all
#      have to be built from source all the time (grpcio takes forever to install)
FROM debian:stretch-slim

# http://bugs.python.org/issue19846
# > At the moment, setting "LANG=C" on a Linux system *fundamentally breaks Python 3*, and that's not OK.
ENV LANG C.UTF-8

# https://support.circleci.com/hc/en-us/articles/360045268074-Build-Fails-with-Too-long-with-no-output-exceeded-10m0s-context-deadline-exceeded-
ENV PYTHONUNBUFFERED=1

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
      libenchant-dev \
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
      libssh-dev \
      libssl1.0-dev \
      patch \
      python-openssl\
      wget \
      zlib1g-dev \
      clang-format \
      unixodbc-dev \
      libsqliteodbc \
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
  && pyenv install 3.5.10 \
  && pyenv install 3.6.12 \
  && pyenv install 3.7.9 \
  && pyenv install 3.8.6 \
  && pyenv install 3.9.0 \
  # Order matters: first version is the global one
  && pyenv global 3.8.6 2.7.17 3.5.10 3.6.12 3.7.9 3.9.0 \
  && pip install --upgrade pip

CMD ["/bin/bash"]
