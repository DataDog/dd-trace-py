ARG PYTHON_VERSION=3.10
FROM python:${PYTHON_VERSION}

ARG CONTRIB_NAME="flask"
ARG CONTRIB_VERSION="2.0.2"
ARG DD_VERSION="2.17.0"
ENV CONTRIB_NAME=${CONTRIB_NAME}
ENV CONTRIB_VERSION=${CONTRIB_VERSION}
ENV DD_VERSION=${DD_VERSION}

COPY setup.sh .
COPY app.py .
COPY requirements.txt .

RUN pip install --upgrade pip
RUN ./setup.sh
RUN pip install -r requirements.txt

ENTRYPOINT ddtrace-run flask run --host 0.0.0.0