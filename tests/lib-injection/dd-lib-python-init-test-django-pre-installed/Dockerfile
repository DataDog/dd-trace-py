FROM python:3.9

ENV PYTHONUNBUFFERED 1
ENV DJANGO_SETTINGS_MODULE django_app
WORKDIR /src
ADD . /src
EXPOSE 18080

# Install ddtrace beforehand to ensure it doesn't get overridden.
RUN pip install django==4.1.3 ddtrace==1.12.0

# Many users run a non-root user, ensure this is supported by the injection mechanism
USER 1000
CMD ddtrace-run python -m django runserver 0.0.0.0:18080
