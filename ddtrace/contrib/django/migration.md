# Migrating to the new Django integration

We have shifted from using a Django app style of configuration to using the
method consistent with our other integrations.

## Steps

1. Remove `'ddtrace.config.django'` from `INSTALLED_APPS` in `settings.py`.

2. Replace `DATADOG_TRACE` configuration in `settings.py` according to the table below.

See below for the mapping from old configuration settings to new ones.

| DATADOG_TRACE setting | Replacement         |
|-----------------------|---------------------|
| `AGENT_HOSTNAME` | `DD_AGENT_HOST` environment variable or `tracer.configure(hostname=)` |
| `AGENT_PORT`     | `DD_TRACE_AGENT_PORT` environment variable or `tracer.configure(port=)` |
| `AUTO_INSTRUMENT`| N/A Instrumentation is automatic |
| `INSTRUMENT_CACHE` | N/A Instrumentation is automatic |
| `INSTRUMENT_DATABASE` | N/A Instrumentation is automatic |
| `INSTRUMENT_TEMPLATE` | N/A Instrumentation is automatic|
| `DEFAULT_DATABASE_PREFIX` | `config.django['database_service_name_prefix']` |
| `DEFAULT_SERVICE` | `DD_SERVICE_NAME` environment variable or `config.django['service_name']` |
| `DEFAULT_CACHE_SERVICE` | `config.django['service_name']` |
| `ENABLED` | `tracer.configure(enabled=)` |
| `DISTRIBUTED_TRACING` | `config.django['distributed_tracing_enabled']` |
| `ANALYTICS_ENABLED` | `config.django['analytics_enabled']` |
| `ANALYTICS_SAMPLE_RATE` | `config.django['analytics_sample_rate']` |
| `TRACE_QUERY_STRING` | `config.django['trace_query_string']` |
| `TAGS` | `DD_TRACE_GLOBAL_TAGS` environment variable or `tracer.set_tags()` |
| `TRACER` | N/A - if a particular tracer is required for the Django integration use `Pin.override(Pin.get_from(django), tracer=)` |


## Examples

### Before

```python
# settings.py
INSTALLED_APPS = [
    # your Django apps...
    'ddtrace.contrib.django',
]

DATADOG_TRACE = {
    'AGENT_HOSTNAME': 'localhost',
    'AGENT_PORT': 8126,
    'AUTO_INSTRUMENT': True,
    'INSTRUMENT_CACHE': True,
    'INSTRUMENT_DATABASE': True,
    'INSTRUMENT_TEMPLATE': True,
    'DEFAULT_SERVICE': 'my-django-app',
    'DEFAULT_CACHE_SERVICE': 'my-cache',
    'DEFAULT_DATABASE_PREFIX': 'my-',
    'ENABLED': True,
    'DISTRIBUTED_TRACING': True,
    'ANALYTICS_ENABLED': True,
    'ANALYTICS_SAMPLE_RATE': 0.5,
    'TRACE_QUERY_STRING': None,
    'TAGS': {'env': 'production'},
    'TRACER': 'my.custom.tracer',
}
```

### After

```python
# settings.py
INSTALLED_APPS = [
    # your Django apps...
]

from ddtrace import config, tracer
tracer.configure(hostname='localhost', port=8126, enabled=True)
config.django['service_name'] = 'my-django-app'
config.django['cache_service_name'] = 'my-cache'
config.django['django_service_name_prefix'] = 'my-'
config.django['trace_query_string'] = True
config.django['analytics_enabled'] = True
config.django['analytics_sample_rate'] = 0.5
tracer.set_tags({'env': 'production'})

import my.custom.tracer
from ddtrace import Pin
import django
Pin.override(Pin.get_from(django), tracer=my.custom.tracer)
```
