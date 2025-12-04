# dd-otel-instrumentor

A subset of Datadog's `dd-trace-py` library that provides integration instrumentation compatible with OpenTelemetry SDK.

## Overview

This package provides Datadog's instrumentation integrations (like `httpx`, `requests`, etc.) in a form that works with OpenTelemetry SDK instead of the Datadog tracer. It's useful when you want to:

- Use DD integrations with OTel SDK directly
- Reduce package footprint by excluding DD-specific components
- Develop/test integrations in isolation from the full tracer

## Key Features

- **No `ddtrace` dependency**: Standalone package that doesn't require the full `dd-trace-py`
- **Same import paths**: Uses the `ddtrace` namespace so integrations work unchanged
- **OTel SDK compatible**: Creates OTel spans instead of DD spans

## Installation

```bash
# Basic installation
pip install dd-otel-instrumentor

# With httpx integration
pip install dd-otel-instrumentor[httpx]

# With all integrations
pip install dd-otel-instrumentor[all]
```

## Development Workflow

This package shares source code with the main `dd-trace-py` repository to avoid duplication. The `ddtrace/` files are **synced automatically at build time** - they are not stored in this directory.

### 1. Edit files in the main `ddtrace/` directory

Make your changes to the source files in the main repository:
- `ddtrace/contrib/compat/` - OTel compatibility layer
- `ddtrace/contrib/internal/<integration>/` - Integration patches
- etc.

### 2. Install/rebuild the package

The `setup.py` automatically syncs required files from `ddtrace/` when you install:

```bash
# Install in development mode (syncs files automatically)
pip install -e ".[dev,all]"

# Or rebuild after changes
pip install -e ".[dev,all]" --no-build-isolation
```

### 3. Test the package

```bash
pytest tests/
```

### Adding New Files to Sync

If you need to include additional files, edit `setup.py` and add the relative path to `FILES_TO_SYNC`:

```python
FILES_TO_SYNC = [
    # ... existing files ...
    "contrib/internal/your_new_integration/__init__.py",
    "contrib/internal/your_new_integration/patch.py",
]
```

## Architecture

```
dd_otel_instrumentor/
├── pyproject.toml             # Package configuration
├── setup.py                   # Build script (syncs ddtrace files)
├── .gitignore                 # Excludes ddtrace/ from git
├── README.md
├── dd_otel_instrumentor/      # Main package with patch() API
│   └── __init__.py            # patch(), unpatch(), etc.
└── ddtrace/                   # Synced at build time (not in git)
    ├── contrib/
    │   ├── compat/            # OTel compatibility layer
    │   └── internal/          # Integration patches
    ├── ext/                   # Extension types (HTTP, net, etc.)
    ├── internal/              # Core utilities
    └── propagation/           # HTTP propagation
```

## Usage Example

```python
# Set up OpenTelemetry SDK
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

trace.set_tracer_provider(TracerProvider())
trace.get_tracer_provider().add_span_processor(
    SimpleSpanProcessor(ConsoleSpanExporter())
)

# Patch httpx with DD instrumentation (automatically enables OTel mode)
from dd_otel_instrumentor import patch
patch("httpx")

# Make requests - spans are created with OTel SDK
import httpx
response = httpx.get("https://example.com")
```

You can also patch multiple integrations at once:

```python
from dd_otel_instrumentor import patch
patch("httpx", "requests", "flask")
```

## Included Integrations

Currently supported integrations:
- `httpx` - HTTP client

More integrations will be added as they are made OTel-compatible.

## Relationship to dd-trace-py

This package is generated from `dd-trace-py` source code. The canonical source of truth is always the main repository. Changes should be made in the main `ddtrace/` directory.

**The `src/ddtrace/` directory is auto-generated** at build time and excluded from git. Do not edit those files directly.

## License

BSD-3-Clause (same as dd-trace-py)

