FROM python:3.11

ENV PYTHONUNBUFFERED 1
ENV DD_INJECTION_ENABLED service_name,tracer,true
ENV DD_PROFILING_ENABLED true # profiling will crash the service on load if it does not pickup autoinjection correctly
ENV DD_TELEMETRY_FORWARDER_PATH ../datadog-lib/telemetry-forwarder.sh
# intentionally redundant in this test
ENV DD_INJECT_FORCE 1
ENV DJANGO_SETTINGS_MODULE django_app
WORKDIR /src
ADD . /src
EXPOSE 18080
RUN pip install django==4.1.3

CMD python -m django runserver 0.0.0.0:18080
