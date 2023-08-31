FROM python:3.9

ENV PYTHONUNBUFFERED 1
ENV DJANGO_SETTINGS_MODULE django_app
WORKDIR /src
ADD . /src
EXPOSE 18080
RUN pip install django==4.1.3 uvicorn==0.20.0
CMD uvicorn --host 0.0.0.0 --port 18080 django_app:application
