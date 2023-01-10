FROM ghcr.io/datadog/dd-trace-py/testrunner:989d6f118d180321399dbc4013467b8eab043396

ENV PYTHONUNBUFFERED 1
ENV DJANGO_SETTINGS_MODULE django_app
WORKDIR /src
ADD . /src
EXPOSE 18080
RUN pyenv global 3.10.3
RUN python3.10 -m pip install django==4.1.3
CMD python3.10 -m django runserver 0.0.0.0:18080
