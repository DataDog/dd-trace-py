# dd-trace-py Project Guide

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

---

<!-- Add more skills below as they are created -->
