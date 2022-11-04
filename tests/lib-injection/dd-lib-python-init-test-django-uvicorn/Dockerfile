FROM python:3.9

ENV PYTHONUNBUFFERED 1
ENV DJANGO_SETTINGS_MODULE django_app
WORKDIR /src
ADD . /src
RUN pip install django uvicorn
EXPOSE 18080
CMD uvicorn --host 0.0.0.0 --port 18080 django_app:application
