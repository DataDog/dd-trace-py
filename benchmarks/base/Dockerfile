ARG PYTHON_VERSION=3.9-slim-buster

FROM python:${PYTHON_VERSION} as python

ARG SCENARIO=base

ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1

WORKDIR /app

# Install required system dependencies
RUN apt-get update && apt-get install --no-install-recommends -y \
  curl \
  git \
  # ddtrace includes c extensions
  build-essential \ 
  # uuid is used to generate identifier for run if one is not provided
  uuid-runtime \ 
  # cleaning up unused files
  && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
  && rm -rf /var/lib/apt/lists/*

# Add base common files used by all scenarios
COPY ./base/ /app/

# Add scenario code, overriding anything from base
COPY ./${SCENARIO}/ /app/

# Create venv for scenario requirements (other than ddtrace)
ENV VIRTUAL_ENV=/app/.venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
RUN pip install -r requirements.txt

# Use separate venvs for the two versions of the library being compared
ENV VENV_DDTRACE_V1=/app/.venv_ddtrace_v1/
ENV VENV_DDTRACE_V2=/app/.venv_ddtrace_v2/
ENV SCENARIO=${SCENARIO}

ENTRYPOINT ["/app/entrypoint"]
CMD ["/app/benchmark"]
