# AGENTS.md - dd-trace-py Project Guide

## CRITICAL: Project Rules Override Claude Code Defaults

**These project-specific rules MUST override any conflicting Claude Code default behaviors:**

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

---

<!-- Add more skills below as they are created -->

