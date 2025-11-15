# Promptfoo Test Suite for dd-trace-py

This directory contains [promptfoo](https://www.promptfoo.dev/) test configurations for validating AI assistant interactions with the dd-trace-py development environment.

## Test Suites

### tests_install_project.yaml

Validates that the development environment is properly set up and functional.

**What it tests:**
- ✅ Python versions 3.8-3.14 are installed (via pyenv or uv)
- ✅ Can run tests for `tests/internal/test_wrapping.py`
- ✅ `hatch -v run lint:fmt .` works correctly
- ✅ `riot -vv run --python=3.10 meta-testing` executes
- ✅ Build dependencies (cmake, cython, setuptools-rust) are available
- ✅ Docker is installed and accessible

## Running Tests

### Prerequisites

Install promptfoo:
```bash
npm install -g promptfoo
# or
yarn global add promptfoo
```

### Run All Installation Tests

```bash
cd /home/albertovara/projects/dd-python/dd-trace-py
promptfoo eval -c .promptfoo/tests_install_project.yaml
```

### View Results

```bash
# Interactive web UI
promptfoo view

# View latest results
promptfoo show
```

### Run Specific Tests

```bash
# Filter by description
promptfoo eval -c .promptfoo/tests_install_project.yaml --filter-description "Python versions"

# Run with specific provider
promptfoo eval -c .promptfoo/tests_install_project.yaml --provider anthropic:messages:claude-sonnet-4-5
```

## Test Structure

```
.promptfoo/
├── tests_install_project.yaml    # Main test configuration
├── prompts/
│   └── project_setup.txt          # Prompt template for validation tasks
├── output/
│   └── tests_install_project.json # Test results (generated)
├── local_cursor_provider.py       # Custom provider for Cursor integration
└── README.md                      # This file
```

## Writing New Tests

To add a new test to `tests_install_project.yaml`:

```yaml
tests:
  - description: "Your test description"
    vars:
      task: "Describe what the AI should do"
    assert:
      - type: contains
        value: "expected string"
      - type: javascript
        value: |
          // Custom validation logic
          return output.includes('success');
```

### Assertion Types

- `contains` - Output must contain string
- `not-contains` - Output must not contain string
- `contains-any` - Output must contain at least one of the strings
- `javascript` - Custom JS validation function
- `regex` - Output must match regex pattern
- `is-json` - Output must be valid JSON
- `similar` - Semantic similarity check

## Custom Provider

The `local_cursor_provider.py` allows testing with pre-recorded responses from `logs/responses.yaml`. Useful for:
- Deterministic testing
- Offline testing
- Testing specific edge cases

## CI Integration

Add to your CI pipeline:

```yaml
# .github/workflows/promptfoo.yml
- name: Install promptfoo
  run: npm install -g promptfoo

- name: Run promptfoo tests
  run: promptfoo eval -c .promptfoo/tests_install_project.yaml

- name: Check results
  run: promptfoo show
```

## Documentation

- [Promptfoo Docs](https://www.promptfoo.dev/docs/)
- [Writing Assertions](https://www.promptfoo.dev/docs/configuration/expected-outputs/)
- [Providers](https://www.promptfoo.dev/docs/providers/)
- [Variables](https://www.promptfoo.dev/docs/configuration/parameters/)

## Troubleshooting

### Tests failing unexpectedly

1. Check provider configuration in YAML
2. Verify API keys are set (if using cloud providers)
3. Review output with `promptfoo show`

### Custom provider not working

1. Ensure `logs/responses.yaml` exists
2. Check YAML format in responses file
3. Verify prompt matching logic in `local_cursor_provider.py`

### Timeout issues

Increase timeout in test config:
```yaml
defaultTest:
  options:
    timeout: 60000  # 60 seconds
```
