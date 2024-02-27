FROM python:3.6

ENV PYTHONUNBUFFERED 1
ENV DJANGO_SETTINGS_MODULE django_app
WORKDIR /src
ADD . /src
EXPOSE 18080
RUN pip install django==3.2 ddtrace==1.12.0

CMD ddtrace-run python -m django runserver 0.0.0.0:18080
