FROM python:3.10

ENV PYTHONUNBUFFERED 1
ENV DJANGO_SETTINGS_MODULE django_app
WORKDIR /src
ADD . /src
EXPOSE 18080
RUN pip install django==4.1.3 uwsgi
CMD uwsgi --http 0.0.0.0:18080 --module django_app:application
