FROM ubuntu:22.04

ARG DD_API_KEY
ARG DD_SITE
ARG DD_SERVICE
ARG DDTRACE_INSTALL_VERSION

ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1
ENV DEBIAN_FRONTEND noninteractive
ENV DD_API_KEY $DD_API_KEY
ENV DD_API_KEY $DD_SITE
ENV DD_SERVICE $DD_SERVICE
ENV DDTRACE_INSTALL_VERSION $DDTRACE_INSTALL_VERSION

WORKDIR /app

COPY ./install-base-deps.sh /app/
RUN ./install-base-deps.sh

COPY ./install-postgres.sh /app/
RUN ./install-postgres.sh

COPY ./install-datadog-agent.sh /app/

RUN ./install-datadog-agent.sh

RUN git clone https://github.com/Kyle-Verhoog/flask-realworld-example-app flask-realworld-example-app
RUN cp -r flask-realworld-example-app/* /app

COPY ./install-app-deps.sh /app/
RUN ./install-app-deps.sh

COPY ./add-fixtures.sh /app/
RUN ./add-fixtures.sh


COPY ./run.sh /app/
CMD ["/app/run.sh"]