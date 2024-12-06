FROM python:3.10

COPY generator.py .
COPY requirements.txt .

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

ENTRYPOINT ddtrace-run generator.py