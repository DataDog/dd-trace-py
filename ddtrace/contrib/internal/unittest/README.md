# Unittest CI Visibility Integration

This package integrates with the Python unittest framework to provide CI Visibility features from Datadog.

## Features

### 1. Early Flake Detection (EFD)

Early Flake Detection (EFD) can help identify flaky tests by automatically retrying tests and detecting inconsistent outcomes. When enabled, each test is executed multiple times to determine if it's flaky.

### 2. Automatic Test Retry (ATR)

Automatic Test Retry (ATR) automatically retries failed tests to reduce false-positive failures. When a test fails, it will be retried a configurable number of times before being reported as a failure.

### 3. Attempt to Fix (ATF)

Attempt to Fix feature provides additional retry attempts with potential fixes or workarounds for failing tests. This can help in environments where tests may fail due to environmental issues.

### 4. Intelligent Test Runner (ITR)

Intelligent Test Runner (ITR) can skip tests that are unlikely to fail based on historical data and code changes. This speeds up test execution by focusing on tests that matter for the current changes.

To mark a test as unskippable (to always run regardless of ITR suggestions), use the `@dd_unskippable` decorator:

```python
from unittest import TestCase
import unittest

class MyTestCase(TestCase):
    @unittest.dd_unskippable
    def test_that_must_always_run(self):
        # This test will always be executed, even when ITR is enabled
        pass
    
    @unittest.dd_unskippable(reason="Critical business logic")
    def test_with_custom_reason(self):
        # This test will always be executed with a custom reason
        pass
```

## Configuration

The features can be enabled or disabled through environment variables:

- `DD_CIVISIBILITY_UNITTEST_ENABLED=true` - Enable CI Visibility for unittest
- `DD_CIVISIBILITY_EFD_ENABLED=true` - Enable Early Flake Detection
- `DD_CIVISIBILITY_ATR_ENABLED=true` - Enable Automatic Test Retry
- `DD_CIVISIBILITY_ITR_ENABLED=true` - Enable Intelligent Test Runner
- `DD_CIVISIBILITY_ATTEMPT_TO_FIX_ENABLED=true` - Enable Attempt to Fix

Additional configuration options are available for each feature:

- `DD_CIVISIBILITY_EFD_RETRY_COUNT=3` - Number of EFD retry attempts
- `DD_CIVISIBILITY_ATR_RETRY_COUNT=3` - Number of ATR retry attempts
- `DD_CIVISIBILITY_ATTEMPT_TO_FIX_RETRY_COUNT=3` - Number of Attempt to Fix retry attempts 