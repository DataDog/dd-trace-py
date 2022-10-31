FROM python:3.9

ENV PYTHONUNBUFFERED 1
ENV DJANGO_SETTINGS_MODULE django_app
WORKDIR /src
ADD . /src
RUN pip install django
EXPOSE 18080
CMD python -m django runserver 0.0.0.0:18080
