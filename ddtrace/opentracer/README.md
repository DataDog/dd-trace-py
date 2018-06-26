# Datadog OpenTracing Tracer

The Datadog opentracer provides an OpenTracing-compatible API to the Datadog
tracer so that you can use the Datadog tracer in your OpenTracing-compatible
applications.

## Installation

To include OpenTracing dependencies in your project with `ddtrace`, ensure you
have the following in `setup.py`.

```python
    install_requires=[
        "ddtrace[opentracing]",
    ],
```

## Quickstart

To jump right in and use the Datadog opentracer with your OpenTracing compatible
application you can run the following:

```sh
$ DATADOG_SERVICE_NAME=$YOUR_SERVICE_NAME ddopentrace-run python $YOUR_APP_PY
```

with your application's service name and whichever python command you use to
execute your application.

For most users you should begin seeing application traces in Datadog.

# Configuration

There are two ways to configure your OpenTracing-compatible application to use
the Datadog opentracer.

- `ddopentrace-run`: provides a hands-off approach to install the tracer which
    requires no additional code

- manual: provides flexibility by configuring the tracer at run-time


### `ddopentrace-run`

`ddopentrace-run` is a command that will install the Datadog opentracer,
overwriting the `opentracing.tracer` reference so that your OpenTracing
compatible application can use Datadog.

#### Usage

For example if your service is called `chat_service` and you run your
application with the command `python app.py` then you could trace your service
with the following command:

```sh
$ DATADOG_SERVICE_NAME=chat_service ddopentrace-run python app.py
```


### Manual

OpenTracing convention for initializing a tracer is to define an initiliazation
method that will configure and instantiate a new tracer and overwrite the global
`opentracing.tracer` reference.

Typically this method looks something like:

```python
from opentracing.ext.scope_manager import ThreadLocalScopeManager
from ddtracer.opentracer import Tracer

def init_tracer(service):
    config = {
        'agent_hostname': 'localhost',
        'agent_port': 8126,
        'debug': False,
        'enabled': True,
        'global_tags': {},
    }
    
    return Tracer(service, config=config, scope_manager=ThreadLocalScopeManager)
```


### Configuration Options


|       `ddopentrace-run`      | Manual                  | Description                              | Default Value | Required |
|:----------------------------:|-------------------------|------------------------------------------|---------------|----------|
| DATADOG_TRACE_ENABLED        | enabled                 | enable or disable the tracer             | `True`        | No       |
| DATADOG_TRACE_DEBUG          | debug                   | enable debug logging                     | `False`       | No       |
| DATADOG_TRACE_AGENT_HOSTNAME | hostname                | hostname of the Datadog agent to use     | `"localhost"` | No       |
| DATADOG_TRACE_AGENT_PORT     | port                    | port of the Datadog agent to use         | `8126`        | No       |
| DATADOG_SERVICE_NAME         | See `Tracer.__init__()` | service name of the service to be traced | `None`        | Yes      |
|                              | global_tags             | tags to apply to each span               | `{}`          | No       |


## Usage

See our tracing [examples repo](https://github.com/DataDog/trace-examples/tree/master/python)
for concrete, runnable examples of the opentracer.
