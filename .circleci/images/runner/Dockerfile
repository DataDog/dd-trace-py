FROM buildpack-deps:xenial

# Install required packages
RUN set -ex; \
        apt-get update; \
        apt-get install -y --no-install-recommends \
            libmemcached-dev \
            locales \
            jq; \
        rm -rf /var/lib/apt/lists/*;

# If we don't set a locale supporting UTF8 the installation of some python
# packages fails
RUN locale-gen en_US.UTF-8
ENV LANG en_US.UTF-8
ENV LANGUAGE en_US:en
ENV LC_ALL en_US.UTF-8

# Install pyenv
RUN curl -L https://raw.githubusercontent.com/pyenv/pyenv-installer/master/bin/pyenv-installer | sh
ENV PATH /root/.pyenv/shims:/root/.pyenv/bin:$PATH

# Install all required python versions
RUN pyenv install 2.7.12
RUN pyenv install 3.4.4
RUN pyenv install 3.5.2
RUN pyenv install 3.6.1
RUN pyenv global 2.7.12 3.4.4 3.5.2 3.6.1

# Install tox
RUN pip install --upgrade pip
RUN pip install tox
