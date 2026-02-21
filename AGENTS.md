# AGENTS.md - dd-trace-py Project Guide

This is the single source of truth for all AI coding assistants working on dd-trace-py.
Tool-specific entry points (`.claude/CLAUDE.md`, `.cursor/rules/dd-trace-py.mdc`) import this file.

## Project Context

`dd-trace-py` is the official Datadog library for tracing and monitoring Python applications. It provides developers with deep visibility into their application's performance by enabling distributed tracing, continuous profiling, and error tracking.

The library offers automatic instrumentation for a wide variety of popular Python frameworks and libraries, such as Django, Flask, Celery, and SQLAlchemy. This allows for quick setup and immediate insights with minimal code changes. For more specific use cases, `dd-trace-py` also provides a comprehensive manual instrumentation API.

Key features include:
- **Distributed Tracing:** Trace requests across service boundaries to understand the full lifecycle of a request in a microservices architecture.
- **Continuous Profiling:** Analyze code-level performance in production to identify and optimize CPU and memory-intensive operations.
- **Error Tracking:** Capture and aggregate unhandled exceptions and errors, linking them to specific traces for easier debugging.
- **Application Security Monitoring (ASM):** Detect and protect against threats and vulnerabilities within your application.

## Critical Architecture Decisions

### Automatic Instrumentation via Monkey-Patching
`dd-trace-py` heavily relies on monkey-patching for its automatic instrumentation. This allows the library to trace a wide range of standard libraries and frameworks without requiring any code changes from the user. This is a powerful feature that makes the library easy to adopt, but it's also a critical piece of architecture to be aware of when debugging or extending the tracer.

### Performance First
The tracer is designed to run in high-throughput production environments. This means performance is a primary concern. Key decisions to achieve this include:
1. **Core components written in C/Cython:** For performance-critical code paths.
2. **Low-overhead sampling:** To reduce the performance impact of tracing.
3. **Efficient serialization:** Using `msgpack` to communicate with the Datadog Agent.

### Extensible Integrations
The library is designed to be easily extensible with new "integrations" for libraries that are not yet supported. The integration system provides a clear pattern for adding new instrumentation.

### Configuration via Environment Variables
To make it easy to configure the tracer in various environments (local development, CI, production containers, etc.), the primary method of configuration is through environment variables.

## Project Rules

1. **Testing**: NEVER run `pytest` directly. ALWAYS use the `run-tests` skill (which uses `scripts/run-tests`). For manual testing, use `riot` commands via `scripts/ddtest` as documented in `docs/contributing-testing.rst`.
2. **Linting**: NEVER use raw linting tools. ALWAYS use the `lint` skill and project-specific `hatch run lint:*` commands.
3. **Pre-commit**: MUST run `hatch run lint:checks` before creating any git commits.
4. **Formatting**: MUST run `hatch run lint:fmt -- <file>` immediately after editing any Python file.

**Never:**
1. Change public API contracts (breaks real applications)
2. Commit secrets (use environment variables)
3. Assume business logic (always ask)
4. Remove AIDEV- comments without instruction
5. Skip linting before committing
6. Check and remove unexpected prints

**Always:**
1. Use the run-tests skill for test execution
2. Use the lint skill before committing
3. Ask when unsure about implementation details
4. Update AIDEV- anchors when modifying related code
5. Consider performance impact (this runs in production)
6. Consider architecture (try to use well-established patterns for the problem at hand)

## Code Style and Patterns

### AIDEV Anchor Comments

Add specially formatted comments throughout the codebase, where appropriate, as inline knowledge that can be easily `grep`ped for.

**Guidelines:**

- Use `AIDEV-NOTE:`, `AIDEV-TODO:`, or `AIDEV-QUESTION:` (all-caps prefix) for comments aimed at AI and developers.
- **Important:** Before scanning files, always first try to **grep for existing anchors** `AIDEV-*` in relevant subdirectories.
- **Update relevant anchors** when modifying associated code.
- **Do not remove `AIDEV-NOTE`s** without explicit human instruction.
- Add relevant anchor comments whenever a file or piece of code is:
  * too complex, or
  * very important, or
  * confusing, or
  * could have a bug

## PR Guidelines

This repository follows the contribution workflow defined in **`docs/contributing.rst`**.
The sections **"Pull Request Requirements"** and **"Branches and Pull Requests"** are the authoritative source of truth for all PRs.

The guidelines below summarize and clarify the expectations for authors and reviewers.

### Pull Request Structure

Every Pull Request **must** include a description formatted with the
Markdown structure in `.github/PULL_REQUEST_TEMPLATE.md`.

### Requirements

* Use **imperative mood** for the title (e.g., "Add support for X").
* Provide context for the change and link any relevant issues or JIRA tickets.
* Include a clear testing plan and indicate new or updated tests.

### When Assisting with Pull Requests

* Validate that all guidelines in this file and `docs/contributing.rst` are followed.
* Point out missing sections, invalid formatting, missing changelog, weak motivation, missing tests, or backward-compatibility risks.
* When generating a new PR description, always output it in the required Markdown block format.

## Skills

This project has custom skills that provide specialized workflows. **Always check if a skill exists before using lower-level tools.**

### run-tests

**Use whenever:** Running any tests, validating code changes, or when "test" is mentioned.

**Purpose:** Intelligently runs the test suite using `scripts/run-tests`:
- Discovers affected test suites based on changed files
- Selects minimal venv combinations (avoiding hours of unnecessary test runs)
- Manages Docker services automatically
- Handles riot/hatch environment setup

**Never:** Run pytest directly - this bypasses the project's test infrastructure. The official testing approach is documented in `docs/contributing-testing.rst`.

**Usage:** Use the Skill tool with command "run-tests"

### lint

**Use whenever:** Formatting code, validating style/types/security, or before committing changes.

**Purpose:** Runs targeted linting and code quality checks using `hatch run lint:*`:
- Formats code with `ruff check` and `ruff format`
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

### find-cpython-usage

**Use whenever:** Adding support for a new Python version or investigating CPython API dependencies.

**Purpose:** Identifies all CPython internal headers and structures used in the codebase:
- Searches for CPython header includes
- Finds struct field accesses
- Documents CPython internals we depend on

**Usage:** Use the Skill tool with command "find-cpython-usage"

### compare-cpython-versions

**Use whenever:** Comparing CPython source code between two Python versions to identify changes.

**Purpose:** Compares CPython headers and structs between versions:
- Uses git diff or manual diff to compare versions
- Identifies breaking changes and API modifications
- Assesses impact on our codebase

**Usage:** Use the Skill tool with command "compare-cpython-versions"

---

<!-- Add more skills below as they are created -->

## Domain Reference Guides

**Before modifying code in a specific domain, read the corresponding guide first:**

| Domain | Guide | Relevant paths |
|--------|-------|----------------|
| Application Security (AppSec) | `.cursor/rules/appsec.mdc` | `ddtrace/appsec/`, `tests/appsec/` |
| IAST | `.cursor/rules/iast.mdc` | `ddtrace/appsec/_iast/`, `tests/appsec/iast*/`, `tests/appsec/integrations/` |
| Repository Structure | `.cursor/rules/repo-structure.mdc` | (any, for orientation) |
| Linting | `.cursor/rules/linting.mdc` | (any, before committing) |
| Testing | `.cursor/rules/testing.mdc` | (any, before running tests) |

## Domain Glossary

- **Trace**: A representation of a single request or transaction as it moves through a system. It's composed of one or more spans.
- **Span**: The basic building block of a trace. It represents a single unit of work (e.g., a function call, a database query). Spans have a start and end time, a name, a service, a resource, and can contain metadata (tags).
- **Name (`span.name`)**: A string that represents the name of the operation being measured. It should be a general, human-readable identifier for the type of operation, such as `http.request`, `sqlalchemy.query`, or a function name like `auth.login`. This is typically more general than the `resource` name.
- **Service**: A logical grouping of traces that perform the same function. For example, a web application or a database.
- **Resource**: The specific operation being performed within a service that a span represents. For example, a web endpoint (`/users/{id}`) or a database query (`SELECT * FROM users`).
- **Instrumentation**: The process of adding code to an application to generate traces. This can be done automatically by the library (`ddtrace-run`) or manually using the tracer API.
- **Integration**: A component of `dd-trace-py` that provides automatic instrumentation for a specific library or framework (e.g., Django, `requests`, `psycopg2`).
- **Tracer**: The object that creates and manages spans. It's the main entry point for manual instrumentation.
- **Profiler**: A component that collects data on CPU and memory usage at the code level, helping to identify performance bottlenecks.
- **`ddtrace-run`**: A command-line utility that automatically instruments a Python application by monkey-patching supported libraries at runtime.
- **Tag**: A key-value pair of metadata that can be added to a span to provide additional context for filtering, grouping, or analysis.
