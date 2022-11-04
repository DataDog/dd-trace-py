FROM python:3.9

ENV PYTHONUNBUFFERED 1
ENV DJANGO_SETTINGS_MODULE django_app
WORKDIR /src
ADD . /src
RUN pip install django gunicorn
EXPOSE 18080
CMD gunicorn --bind :18080 django_app:application
