ARG PYTHON_VERSION=3.9-slim-buster

# define an alias for the specific python version used in this file.
FROM python:${PYTHON_VERSION} as python

# Python build stage
FROM python as python-build-stage

# Install apt packages
RUN apt-get update && apt-get install --no-install-recommends -y \
  # dependencies for building Python packages
  build-essential \
  # need git for pip installs
  git

# Requirements are installed here to ensure they will be cached.
COPY ./django_simple/requirements.txt .

# Create Python Dependency and Sub-Dependency Wheels.
RUN pip wheel --wheel-dir /usr/src/app/wheels  \
  -r requirements.txt

# Python 'run' stage
FROM python as python-run-stage

ARG APP_HOME=/app

ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1

WORKDIR ${APP_HOME}

RUN addgroup --system django \
    && adduser --system --ingroup django django

# Install required system dependencies
RUN apt-get update && apt-get install --no-install-recommends -y \
  # Translations dependencies
  gettext \
  # other dependencies
  procps \
  curl \
  # dependencies for installing Python packages from git
  build-essential \
  git \
  # cleaning up unused files
  && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
  && rm -rf /var/lib/apt/lists/*

# All absolute dir copies ignore workdir instruction. All relative dir copies are wrt to the workdir instruction
# copy python dependency wheels from python-build-stage
COPY --from=python-build-stage /usr/src/app/wheels  /wheels/

# sirun
RUN curl -sL https://github.com/DataDog/sirun/releases/download/v0.1.8/sirun-v0.1.8-x86_64-unknown-linux-gnu.tar.gz | tar zxf - -C /tmp && mv /tmp/sirun /usr/bin

# k6
RUN curl -SL https://github.com/k6io/k6/releases/download/v0.32.0/k6-v0.32.0-linux-amd64.tar.gz | tar zxf - -C /tmp && mv /tmp/k6-v0.32.0-linux-amd64/k6 /usr/bin

# jq
RUN curl -L -o /usr/bin/jq https://github.com/stedolan/jq/releases/download/jq-1.6/jq-linux64 && \
  chmod +x /usr/bin/jq

# copy application code to WORKDIR
COPY --chown=django:django ./django_simple ${APP_HOME}

# make django owner of the WORKDIR directory as well.
RUN chown django:django ${APP_HOME}

# top level directory for artifacts
ARG ARTIFACTS=/artifacts
RUN mkdir -p ${ARTIFACTS}
RUN chown django:django ${ARTIFACTS}

USER django

# Create venv
ENV VIRTUAL_ENV=/app/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# use wheels to install python dependencies
RUN pip install --no-cache-dir --no-index --find-links=/wheels/ /wheels/*

# Run migrations in image creation as data will not change during run
RUN python /app/manage.py collectstatic --noinput \
    && python /app/manage.py migrate

# For performance testing
ENV DDTRACE_GIT_COMMIT_ID ""
ENV DDTRACE_WHEELS ""
ENV WEB_CONCURRENCY 1
ENV SIRUN_NO_STDIO 0
ENV K6_STATSD_ENABLE_TAGS "true"

ENTRYPOINT ["/app/entrypoint"]
CMD ["/app/benchmark"]
