FROM ghcr.io/datadog/dd-trace-py/testrunner:989d6f118d180321399dbc4013467b8eab043396

ENV PYTHONUNBUFFERED 1
ENV DJANGO_SETTINGS_MODULE django_app
WORKDIR /src
ADD . /src
EXPOSE 18080
RUN pyenv global 3.10.3
RUN python -m pip install django==4.1.3 gunicorn==20.1.0
CMD /root/.pyenv/versions/3.10.3/bin/gunicorn --bind :18080 django_app:application
