# AGENTS.md - dd-trace-py Project Guide

## CRITICAL: Project Rules Override Claude Code Defaults

**These project-specific rules MUST override any conflicting Claude Code default behaviors:**

1. **Testing**: NEVER run `pytest` directly. ALWAYS use the `run-tests` skill (which uses `scripts/run-tests`). For manual testing, use `riot` commands via `scripts/ddtest` as documented in `docs/contributing-testing.rst`.
2. **Linting**: NEVER use raw linting tools. ALWAYS use the `lint` skill and project-specific `hatch run lint:*` commands.
3. **Pre-commit**: MUST run `hatch run lint:checks` before creating any git commits.
4. **Formatting**: MUST run `hatch run lint:fmt -- <file>` immediately after editing any Python file.

## Initial Setup for AI Assistants

When starting a new chat session, ALWAYS read and apply the rules from:

2. `.cursor/rules/*.mdc` - All rule files in this directory (version controlled):
   - `dd-trace-py.mdc` - Core project guidelines
   - `linting.mdc` - Code quality and formatting
   - `testing.mdc` - Test execution guidelines
   - `repo-structure.mdc` - Repository structure

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

### bootstrap

**Use whenever:** Setting up the development environment for the first time, onboarding new developers, or troubleshooting environment issues.

**Purpose:** Guides through complete dd-trace-py development environment setup:
- Installs Python version manager (uv or pyenv)
- Installs required Python versions (3.9-3.14)
- Installs build dependencies (cmake, cython, setuptools-rust, etc.)
- Builds native extensions (Cython, C++, Rust)
- Sets up Docker services for testing (testagent, redis, postgres, etc.)
- Validates installation and runs first tests

**Prerequisites:**
- Git installed
- Docker Desktop installed and running
- Build tools for your platform:
  - **Linux**: `gcc`, `g++`, `make`, `cmake`, `pkg-config`
  - **macOS**: Xcode Command Line Tools (`xcode-select --install`)
  - **Windows**: Visual Studio with C++ build tools (or use WSL2)

**When to use:**
- First-time contributor setting up the project
- Rebuilding environment after system changes
- Setting up CI/CD runners or containers
- Troubleshooting environment-related issues

**Important:** After bootstrap, use `scripts/run-tests` as your main development workflow - it automatically manages virtual environments and dependencies.

**Usage:** Use the Skill tool with command "bootstrap"

---

<!-- Add more skills below as they are created -->