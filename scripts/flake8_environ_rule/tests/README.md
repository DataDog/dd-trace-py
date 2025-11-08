# Tests for environ_checker flake8 plugin

This directory contains test files for the `environ_checker` flake8 plugin.

## test_all_patterns.py

A dummy file containing examples of ALL environment variable access patterns that should be detected and flagged by the environ_checker plugin.

### Usage

```bash
# Run the test to verify all patterns are caught
hatch run lint:flake8 --select=ENV001 scripts/flake8_environ_rule/tests/test_all_patterns.py
```

### Expected Result

All lines containing environment variable access should be flagged with ENV001 errors. The test covers:

1. **Direct os.environ access patterns**: method calls (get, copy, clear, update, pop, setdefault, keys, values, items)
2. **Membership tests and iteration**: `in` operator, `for` loops
3. **Direct environ usage**: imported `environ` from os
4. **Aliased environ usage**: `from os import environ as env_dict`
5. **os.getenv function calls**: direct calls with and without defaults
6. **Direct getenv calls**: imported `getenv` from os
7. **Aliased getenv calls and direct attribute access**: including `os.getenv` attribute references

### Pattern Count

The test should detect exactly 28 violations across all the different access patterns.
