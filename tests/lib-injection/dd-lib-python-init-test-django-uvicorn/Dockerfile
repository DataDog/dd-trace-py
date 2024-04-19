FROM python:3.12

ENV PYTHONUNBUFFERED 1
ENV DJANGO_SETTINGS_MODULE django_app
WORKDIR /src
ADD . /src
EXPOSE 18080
RUN pip install django==4.2.10 uvicorn==0.27.1
CMD uvicorn --host 0.0.0.0 --port 18080 django_app:application
