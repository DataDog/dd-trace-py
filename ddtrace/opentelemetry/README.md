# Opentelemetry Setup Guide

With the `ddtrace.opentelemetry` package, Opentelemetry users can use the [Opentelemetry API](https://github.com/open-telemetry/opentelemetry-python/tree/main/opentelemetry-api) to generate and submit traces to the Datadog Agent. Below are instructions to install and configure opentelemetry support from the following branch: [opentelemetry-api-support](https://github.com/DataDog/dd-trace-py/tree/opentelemetry-api-support)

Note - ddtrace support for the `opentelemetry-api` package is currently in development and can only be installed from the feature branch linked above.

## Requirements

- python version >= 3.7
- opentelmetry-api >= 1.0
- pip >= 18


## Opentelemetry Support

The `ddtrace.opentelemetry` supports tracing applications using all operations defined in the [opentelmetry python api](https://opentelemetry.io/docs/instrumentation/python/) except for creating span links, generating span events, and using the Metrics API. These operations are not yet supported. Below is a non-exhaustive list of supported operations:
 - Creating a span
 - Activating a span
 - Setting attributes on Span
 - Setting error types and error messages on spans
 - Manual span parenting
 - Distributed tracing (Injecting/Extracting trace headers)


## Installation

### Install ddtrace

`pip install git+https://github.com/DataDog/dd-trace-py@opentelemetry-api-support#egg=ddtrace`


## Usage

Ensure the datadog agent is installed and running: https://docs.datadoghq.com/agent/


### Manual Instrumentation

1. In your application code import and set the [Datadog Opentelemetry TraceProvider](https://github.com/DataDog/dd-trace-py/blob/f637975987d7dc4d31e065b7351e2515f9310671/ddtrace/opentelemetry/__init__.py#L5). This should be done on application startup, ideally where ddtrace patching and the datadog tracer are configured. 
2. Set the following environment variable: `OTEL_PYTHON_CONTEXT=ddcontextvars_context`. This will override the default otel contextmanager and ensure all spans are generated with the correct parenting.

Ex:
```
import os
from opentelemetry.trace import set_tracer_provider
from ddtrace.opentelemetry import TracerProvider
os.environ["OTEL_PYTHON_CONTEXT"] = "ddcontextvars_context"
set_tracer_provider(TracerProvider())
```


### Automatic Instrumentation

Run your application with the following command: `ddtrace-run <my_program>`. The `ddtrace-run` command will initialize and set the ddtrace Opentelemetry [TraceProvider](https://github.com/DataDog/dd-trace-py/blob/f637975987d7dc4d31e065b7351e2515f9310671/ddtrace/bootstrap/sitecustomize.py#L125) and the `OTEL_PYTHON_CONTEXT` environment variable to supported values. 

Datadog Opentelemetry support can be disabled by setting the following environment variable: `DD_TRACE_OTEL_ENABLED=False`


### Sample Flask App


```python
import flask
import ddtrace
import opentelemetry
# # The following operations are required when manual instrumentation is used
# from opentelemetry.trace import set_tracer_provider
# from ddtrace.opentelemetry import TracerProvider
# set_tracer_provider(TracerProvider())
# ddtrace.patch(flask=True)
# import os
# os.environ["OTEL_PYTHON_CONTEXT"] = "ddcontextvars_context"
app = flask.Flask(__name__)
@app.route("/")
def index():
    return "hello"
@app.route("/otel")
def otel():
    oteltracer = opentelemetry.trace.get_tracer(__name__)
    with oteltracer.start_as_current_span("otel-flask-manual-span"):
        with ddtrace.tracer.trace("nested-ddtrace-span"):
            import time
            time.sleep(0.05)
            return "otel", 200
```

#### Sample trace

![trace](https://raw.githubusercontent.com/DataDog/dd-trace-py/c2e8b332be75d4d95b701c623d406ba49a775e08/ddtrace/opentelemetry/opentelemetry-trace-sample.png)


## Troubleshooting

The Opentelemetry API does not support using multiple sdks in one runtime. Although ddtrace Opentelemetry support is compatibable with the opentelemetry-sdk there is potentical for conflicts if certain default configurations are changed or third party opentelemetry sdks are installed. To avoid potentical compatibility issues uninstall all third party opentelemetry sdks (if possible) and follow the instructions below:
 - Ensure only the [Datadog Opentelemetry TraceProvider](https://github.com/DataDog/dd-trace-py/blob/f637975987d7dc4d31e065b7351e2515f9310671/ddtrace/opentelemetry/__init__.py#L5) is set in your application.
   - The OpenTelemetry API supports [only setting one TracerProvider](https://opentelemetry-python.readthedocs.io/en/latest/api/trace.html?highlight=set_tracer_provider#opentelemetry.trace.set_tracer_provider). Tracer providers can be set via entrypoint, envar, or in code. Attempting to set multiple tracer providers will generate a [warning](https://github.com/open-telemetry/opentelemetry-python/blob/209093bfbe3fae0fc29c693f55d6bb6a2d997aad/opentelemetry-api/src/opentelemetry/trace/__init__.py#L521).
 - Ensure the [OTEL_PYTHON_CONTEXT](https://github.com/open-telemetry/opentelemetry-python/blob/main/opentelemetry-api/src/opentelemetry/environment_variables.py#L32) environment variable is set to `ddcontextvars_context` and the [opentelemetry_context entrypoint](https://github.com/open-telemetry/opentelemetry-python/blob/main/opentelemetry-api/pyproject.toml#L40) contains the following entry: `ddcontextvars_context = ddtrace.opentelemetry._context:DDRuntimeContext`