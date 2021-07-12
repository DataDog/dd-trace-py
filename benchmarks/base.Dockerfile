ARG PYTHON_VERSION=3.9-slim-buster

FROM python:${PYTHON_VERSION} as python

ARG BENCHMARK=sample
ARG ARTIFACTS=/artifacts

ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1

WORKDIR /app

# Install required system dependencies
RUN apt-get update && apt-get install --no-install-recommends -y \
  curl \
  git \
  # cleaning up unused files
  && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
  && rm -rf /var/lib/apt/lists/*

# sirun
RUN curl -sL https://github.com/DataDog/sirun/releases/download/v0.1.8/sirun-v0.1.8-x86_64-unknown-linux-gnu.tar.gz | tar zxf - -C /tmp && mv /tmp/sirun /usr/bin

COPY ./common/entrypoint /app/
COPY ./common/benchmark /app/

# Add benchmark scenario code, overriding anything from common
COPY ./${BENCHMARK}/ /app/

# Create venv
ENV VIRTUAL_ENV=/app/.venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

RUN pip install -r requirements.txt

# For performance testing
ENV DDTRACE_GIT_COMMIT_ID ""
ENV PIP_INSTALL_WHEELS ""
ENV SIRUN_NO_STDIO 0

ENTRYPOINT ["/app/entrypoint"]
CMD ["/app/benchmark"]
