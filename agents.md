# Datadog Python APM Client - Agent Guide

This document provides a guide for an AI agent working within the `dd-trace-py` repository.

## Project Overview

`dd-trace-py` is the official Python library for Datadog APM. It provides Distributed Tracing, Continuous Profiling, Error Tracking, CI Visibility, and more.

### Key Resources
- **Product Documentation:** [Datadog Tracing Docs](https://docs.datadoghq.com/tracing/setup/python/)
- **Library API Documentation:** [ddtrace.readthedocs.io](https://ddtrace.readthedocs.io/)
- **Contribution Guidelines:** [Contributing Docs](https://ddtrace.readthedocs.io/en/stable/contributing.html)

---

## Testing

Tests are managed via `riot` and defined in `riotfile.py`. This file contains the definitions for all test environments (venvs), including their Python versions and dependencies.

### Standard Test Execution

To run a test suite, use the `riot` command. You need to specify the python version (`-p`) and the venv name.

NOTE: Requires internet access to download dependencies during test run.

**Example:**
```bash
# Run the 'requests' test suite on Python 3.8
riot -v run -p 3.8 -s requests
```

### Two-Stage Testing for CI/CD

For environments where dependency installation and test execution must be separate, the `scripts/dd-test.sh` script provides a two-stage workflow.

#### 1. Install Dependencies

First, install the necessary dependencies for a specific `riot` virtual environment. Venvs can be found in the riotfile, named similarly
to the feature / integration., ie requests integration has requests as the venv name.

**Usage:**
```bash
./scripts/dd-test.sh install <venv_name> <python_version>
```

**Example:**
```bash
# Install dependencies for the 'requests' venv on Python 3.8
./scripts/dd-test.sh install requests 3.8
```

#### 2. Run Tests

After the dependencies are installed, you can run the tests in your locked-down environment.

**Usage:**
```bash
./scripts/dd-test.sh run <venv_name> <python_version> [pytest_args...]
```

**Example:**
```bash
# Run all tests for 'requests' on Python 3.8
DD_AGENT_PORT=9126 ./scripts/dd-test.sh run requests 3.8
```

---

## Key Files and Directories

- `ddtrace/`: The main source code for the library.
- `tests/`: Contains all unit, integration, and snapshot tests.
  - `tests/contrib/`: Integration-specific tests.
- `riotfile.py`: The central configuration for all `riot` test environments. This is the source of truth for test dependencies and commands.
- `scripts/`: Contains helper and automation scripts.
- `docs/`: Source for the library's API documentation.
