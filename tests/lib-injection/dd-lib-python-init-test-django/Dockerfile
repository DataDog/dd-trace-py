FROM python:3.11

ENV PYTHONUNBUFFERED 1
ENV DJANGO_SETTINGS_MODULE django_app
WORKDIR /src
ADD . /src
EXPOSE 18080
RUN pip install django==4.1.3

CMD python -m django runserver 0.0.0.0:18080
