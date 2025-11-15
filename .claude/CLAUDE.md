# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**dd-trace-py** is Datadog's official APM (Application Performance Monitoring) library for Python, providing:
- Distributed Tracing
- Continuous Profiling
- Application Security Monitoring (AppSec/IAST)
- Error Tracking
- Test Optimization (CI Visibility)
- Dynamic Instrumentation
- LLM Observability

**Key architectural principles:**
- **Automatic instrumentation via monkey-patching** - Core design decision enabling zero-code-change tracing
- **Performance-first** - C/Cython for hot paths, efficient msgpack serialization, low-overhead sampling
- **Configuration via environment variables** - Primary config method (DD_* prefix)
- **Extensible integrations** - Clear patterns for adding new library instrumentation (107+ integrations)

## Skills

This project has custom skills that provide specialized workflows. **Always check if a skill exists before using lower-level tools.**

### run-tests

**Use whenever:** Running any tests, validating code changes, or when "test" is mentioned.

**Purpose:** Intelligently runs the test suite using `scripts/run-tests`:
- Discovers affected test suites based on changed files
- Selects minimal venv combinations (avoiding hours of unnecessary test runs)
- Manages Docker services automatically
- Handles riot/hatch environment setup

**Never:** Run pytest directly or use `hatch run tests:test` - these bypass the project's test infrastructure.

**Usage:** Use the Skill tool with command "run-tests"

### lint

**Use whenever:** Formatting code, validating style/types/security, or before committing changes.

**Purpose:** Runs targeted linting and code quality checks using `hatch run lint:*`:
- Formats code with Black and Ruff auto-fixes
- Validates style, types, and security
- Checks spelling and documentation
- Validates test infrastructure (suitespec, riotfile, etc.)
- Supports running all checks or targeting specific files

**Common Commands:**
- `hatch run lint:fmt -- <file>` - Format a specific file after editing (recommended after every edit)
- `hatch run lint:typing -- <file>` - Type check specific files
- `hatch run lint:checks` - Run all quality checks (use before committing)
- `hatch run lint:security -- -r <dir>` - Security scan a directory

**Never:** Skip linting before committing. Always run `hatch run lint:checks` before pushing.

**Usage:** Use the Skill tool with command "lint"

### bootstrap

**Use whenever:** Setting up the development environment, onboarding new developers, or troubleshooting environment issues.

**Purpose:** Guides through complete development environment setup:
- Installing Python version managers (pyenv or uv)
- Installing Python versions 3.8-3.14
- Installing build dependencies (CMake, Cython, Rust)
- Building native extensions (C/C++/Cython/Rust)
- Setting up Docker services
- Verifying installation

**Prerequisites:**
- Git, Docker, and platform-specific build tools
- Covers macOS, Linux, and Windows (WSL2 recommended)

**Usage:** Use the Skill tool with command "bootstrap"

---

## Architecture

### Core Components

**ddtrace/_trace/** - Core tracing engine (private implementation)
- `tracer.py` - Main Tracer class
- `span.py` - Span lifecycle and operations
- `context.py` - Context propagation
- `processor/` - Trace processing pipeline

**ddtrace/contrib/** - 107+ library integrations
- `internal/` - Shared utilities for integrations (asgi.py, wsgi.py, utils.py)
- Individual integration packages (flask, django, celery, openai, langchain, etc.)
- `integration_registry/` - Auto-discovery and management system
- **Pattern**: Each integration has `patch.py` for monkey-patching implementation

**ddtrace/appsec/** - Application Security
- `_iast/` - Interactive Application Security Testing (taint tracking, vulnerability detection)
- `_ddwaf/` - Web Application Firewall
- `_exploit_prevention/` - Runtime protection
- **Note**: When working on IAST, read `.cursor/context/` docs and `ddtrace/appsec/_iast/__init__.py`

**ddtrace/internal/** - Private implementation utilities
- Performance-critical Cython extensions: `_encoding.pyx`, `_rand.pyx`, `_tagset.pyx`
- Products: `remoteconfig/`, `datastreams/`, `ci_visibility/`, `telemetry/`

**ddtrace/profiling/** - Continuous profiling
- `collector/` - Native & Python collectors
- `exporter/` - Profile data export (pprof format)
- Cython optimizations: `_threading.pyx`

**ddtrace/llmobs/** - LLM Observability
- `_integrations/` - OpenAI, Anthropic, Langchain, Bedrock, etc.
- `_evaluators/` - Evaluation metrics

**ddtrace/debugging/** - Dynamic Instrumentation
- `_probe/` - Debug probe management
- `_products/` - Product-specific debugging features

### Product Protocol

Modular feature system via entry points defined in `pyproject.toml`. Products implement lifecycle methods:
- `start()`, `stop()`, `restart()`, etc.
- Examples: telemetry, remoteconfig, profiling, appsec

### Native Code Components

**src/native/** - C/C++ extensions for maximum performance
- When working with `.cpp`, `.c`, `.h` files:
  1. **Prefer**: Native types (string, int, char)
  2. **If needed**: CPython with PyObjects (careful with memory/refcounting)
  3. **Last resort**: Pybind11

**Cython modules** (`.pyx` files):
- Used for performance-critical hot paths
- Examples: encoding, random generation, tagset operations

**Build system**: setuptools + CMake + setuptools-rust for multi-language builds

### Test Architecture

**Intelligent suite discovery** via `tests/suitespec.yml`:
- Components define file patterns that trigger specific test suites
- Components prefixed with `$` (e.g., `$harness`, `$setup`) apply to ALL suites
- Maps changed files → affected test suites → available venvs

**Test suites** include:
- `tracer` - Core tracing functionality
- `internal` - Internal components
- `contrib::<name>` - Individual integrations (e.g., `contrib::flask`)
- `telemetry`, `profiling`, `appsec`, `llmobs` - Product-specific tests
- `integration_agent`, `integration_testagent` - Agent integration and snapshot tests

**Riot venv system**:
- Each suite has multiple venvs (Python version × package versions)
- Example: Flask might have 20+ venvs (Python 3.8-3.13 × Flask 1.x-3.x)
- Venvs identified by hash for quick selection

**Docker services**: Managed automatically per suite (redis, postgres, testagent, etc.)

### Configuration Files

- `pyproject.toml` - Project metadata, dependencies, tool configs
- `hatch.toml` - Lint/format environments and scripts
- `riotfile.py` - 3000+ lines defining test matrix
- `tests/suitespec.yml` - Component-to-suite mapping
- `setup.py` - Complex build with Cython, CMake, Rust extensions
- `docker-compose.yml` - Dev services (redis, postgres, testagent)

## Development Patterns

### Anchor Comments

Use specially formatted comments for AI/developer context:
- `AIDEV-NOTE:` - Important implementation details
- `AIDEV-TODO:` - Tasks to be done
- `AIDEV-QUESTION:` - Areas needing clarification

**Important**:
- Always grep for existing `AIDEV-*` anchors in relevant subdirectories before scanning files
- Update relevant anchors when modifying associated code
- Never remove `AIDEV-NOTE`s without explicit instruction

### Adding New Integrations

1. Create structure in `ddtrace/contrib/internal/<library>/`
2. Implement `patch.py` with monkey-patching logic
3. Add metadata in `__init__.py`
4. Add tests in `tests/contrib/<library>/`
5. Update integration registry if needed

### IAST/AppSec Development

- Read context docs in `.cursor/context/`
- Consider both `ddtrace/appsec/_iast/` and `tests/appsec/iast/`
- Synonyms: IAST = Code Security = Runtime Code Analysis
- C++ tests: `cmake -DCMAKE_BUILD_TYPE=Debug -DPYTHON_EXECUTABLE=python -S ddtrace/appsec/_iast/_taint_tracking -B ddtrace/appsec/_iast/_taint_tracking && make -f ddtrace/appsec/_iast/_taint_tracking/tests/Makefile native_tests`
- Python tests: `python -m pytest -vv -s --no-cov`
- E2E tests use test servers from `tests/appsec/appsec_utils.py`
- E2E tests need testagent: `docker compose up -d testagent`

### Code Style

- All code and docstrings in English
- Avoid one-line comments explaining simple logic
- Always add docstrings to functions (no need to explain return values)
- Format after every edit: `hatch run lint:fmt -- <file>`

## Domain Glossary

- **Trace**: Representation of a single request/transaction through a system (composed of spans)
- **Span**: Basic unit of work (function call, DB query) with start/end time, name, service, resource, metadata
- **Name (span.name)**: General operation identifier (e.g., `http.request`, `sqlalchemy.query`)
- **Service**: Logical grouping of traces performing the same function
- **Resource**: Specific operation within a service (e.g., `/users/{id}`, `SELECT * FROM users`)
- **Instrumentation**: Adding code to generate traces (automatic via `ddtrace-run` or manual via API)
- **Integration**: Component providing automatic instrumentation for a specific library
- **Tracer**: Object that creates and manages spans (main entry point for manual instrumentation)
- **Profiler**: Component collecting CPU/memory usage data at code level
- **ddtrace-run**: CLI utility for automatic instrumentation via monkey-patching
- **Tag**: Key-value metadata added to spans for filtering/grouping/analysis

## Contributing Documentation

**For detailed guidance, refer to these comprehensive contributing docs:**

- **[docs/contributing.rst](docs/contributing.rst)** - Main contributing guide
  - Change process and PR workflow
  - PR requirements and title conventions
  - Backporting and release process
  - How to get started as a contributor

- **[docs/contributing-testing.rst](docs/contributing-testing.rst)** - Testing guidelines
  - What kind of tests to write (unit, integration, e2e)
  - Where to put tests
  - How to run the test suite with `scripts/run-tests`
  - Riot and test environment management

- **[docs/contributing-integrations.rst](docs/contributing-integrations.rst)** - Integration development
  - What is an integration and how to write one
  - Tools: Wrapt, Pin API, Core API
  - Integration skeleton in `templates/integration/`
  - Best practices for invisible instrumentation

- **[docs/contributing-tracing.rst](docs/contributing-tracing.rst)** - Tracing product specifics
  - Service naming conventions
  - Peer.service and schema support
  - OpenTelemetry format compatibility

- **[docs/contributing-design.rst](docs/contributing-design.rst)** - Library design philosophy
  - Parts of the library (products vs integrations vs core)
  - How autoinstrumentation works (bootstrap, import hooks, patching)
  - Separation of concerns between products and integrations

- **[docs/contributing-release.rst](docs/contributing-release.rst)** - Release process
  - Release candidates and testing
  - Performance gates and SLO thresholds
  - Patch release procedures

**When to reference these docs:**
- First time setup → Use the `bootstrap` skill, then read `contributing.rst`
- Starting a new integration → Read `contributing-integrations.rst`
- Writing tests → Read `contributing-testing.rst`
- Understanding architecture → Read `contributing-design.rst`
- Submitting a PR → Read `contributing.rst`
- Working on tracing features → Read `contributing-tracing.rst`

## Critical Rules

**Never:**
1. Change public API contracts (breaks real applications)
2. Commit secrets (use environment variables)
3. Assume business logic (always ask)
4. Remove AIDEV- comments without instruction
5. Skip linting before committing

**Always:**
1. Use the run-tests skill for test execution
2. Use the lint skill before committing
3. Ask when unsure about implementation details
4. Update AIDEV- anchors when modifying related code
5. Consider performance impact (this runs in production)
