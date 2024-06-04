FROM python:3.7

ENV PYTHONUNBUFFERED 1
ENV DD_INJECT_FORCE 1
ENV DJANGO_SETTINGS_MODULE django_app
WORKDIR /src
ADD . /src
EXPOSE 18080
RUN pip install django==3.2 structlog<=19.0

CMD python -m django runserver 0.0.0.0:18080
