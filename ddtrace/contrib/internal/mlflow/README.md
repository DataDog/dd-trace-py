# MLFlow Authentication Plugin

This directory contains the Datadog authentication plugin for MLFlow.

## Overview

The MLFlow authentication plugin automatically injects Datadog API keys into MLFlow HTTP requests. This enables authenticated communication between MLFlow and Datadog-secured endpoints.

## How It Works

The plugin implements the MLFlow `RequestHeaderProvider` interface, which allows custom headers to be injected into all MLFlow HTTP requests. When the `DD_API_KEY` environment variable is set, the plugin automatically adds a `DD-API-KEY` header to every MLFlow request.

## Installation

The plugin is automatically registered when `ddtrace` is installed, via the entry point system in `pyproject.toml`:

```toml
[project.entry-points.'mlflow.request_header_provider']
datadog = "ddtrace.contrib.internal.mlflow.auth_plugin:DatadogHeaderProvider"
```

## Usage

1. Set the `DD_API_KEY` and `DD_APP_KEY` environment variable:
   ```bash
   export DD_API_KEY="your_api_key_here"
   export DD_APP_KEY="your_app_key_here"
   ```

2. Use MLFlow as normal:
   ```python
   import mlflow

   # All MLFlow HTTP requests will now include the DD-API-KEY header
   mlflow.set_tracking_uri("https://your-mlflow-server.com")
   mlflow.log_param("example", "value")
   ```

## Plugin Behavior

- **When `DD_API_KEY` and `DD_APP_KEY` are set**: The plugin is active and injects the `DD-API-KEY` header into all requests
- **When `DD_API_KEY` is not set**: The plugin is inactive and does not modify requests

## Implementation Details

The plugin consists of two main components:

### `auth_plugin.py`

Contains the `DatadogHeaderProvider` class with two required methods:

- `in_context()`: Returns `True` if `DD_API_KEY` is set, `False` otherwise
- `request_headers()`: Returns `{"DD-API-KEY": value}` if the key is set, empty dict otherwise

### `__init__.py`

Provides documentation and module-level information about the integration.

## Testing

Tests are located in `tests/contrib/mlflow/test_auth_plugin.py` and cover:

- Plugin importability
- Context detection with and without API key
- Header generation with various API key values
- Empty header generation when API key is not set

Run tests with:
```bash
pytest tests/contrib/mlflow/
```

## References

- [MLFlow Authentication Plugins Documentation](https://mlflow.org/docs/latest/ml/plugins/#authentication-plugins)
- [MLFlow Request Header Provider Interface](https://mlflow.org/docs/latest/ml/plugins/#request-header-provider)
