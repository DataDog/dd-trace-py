# Instructions

- Create a venv: `python -m venv venv`
- Activate it: `source venv/bin/activate`
- Install the requirements: `pip install -r requirements.txt` (it may take some time)
- Update your API Key in `docker-compose.yaml`
- Stop your datadog-agent
- Start your collector: `docker compose up -d`
- Try to run the app without dd instrumentation: `opentelemetry-instrument python app.py`
- Try to run the app with dd instrumentation:
`EXPERIMENTAL_OTEL_DD_INSTRUMENTATION_ENABLED=True opentelemetry-instrument python app.py`