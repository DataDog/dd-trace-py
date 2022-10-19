FROM python:3.9

ENV PYTHONUNBUFFERED 1
ENV DJANGO_SETTINGS_MODULE django_app
WORKDIR /src
ADD . /src
RUN pip install django uwsgi
EXPOSE 18080
CMD uwsgi --http :18080 --module django_app:application
