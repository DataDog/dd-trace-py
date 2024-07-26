FROM python:3.8-slim

ENV PYTHONUNBUFFERED 1
ENV DD_INJECTION_ENABLED service_name,tracer,true
ENV DD_PROFILING_ENABLED true # profiling will crash the service on load if it does not pickup autoinjection correctly
ENV DD_INJECT_FORCE 1
WORKDIR /src
ADD . /src
EXPOSE 18080
RUN pip install -r requirements.txt

CMD python test.py
