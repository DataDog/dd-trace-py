#!/usr/bin/env python3

from unittest import mock

import pytest

from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings


@pytest.fixture(autouse=True, scope="session")
def mock_check_enabled_features():
    with mock.patch(
        "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
        return_value=TestVisibilityAPISettings(flaky_test_retries_enabled=True),
    ):
        yield
