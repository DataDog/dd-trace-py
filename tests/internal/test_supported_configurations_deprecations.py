"""Tests for the deprecation metadata exposed by _supported_configurations."""
from ddtrace.internal.settings import _supported_configurations as sc


def test_deprecated_configurations_is_a_dict():
    assert isinstance(sc.DEPRECATED_CONFIGURATIONS, dict)


def test_known_deprecated_env_var_has_removal_version():
    info = sc.DEPRECATED_CONFIGURATIONS.get("DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED")
    assert info is not None
    assert info["removal_version"] == "5.0.0"
    assert "128-bit trace ID" in info["extra_message"]


def test_deprecated_alias_in_deprecated_configurations():
    info = sc.DEPRECATED_CONFIGURATIONS.get("DD_TRACE_INFERRED_SPANS_ENABLED")
    assert info is not None
    assert info["replaced_by"] == "DD_TRACE_INFERRED_PROXY_SERVICES_ENABLED"


def test_canonical_of_deprecated_alias_is_not_itself_deprecated():
    # DD_TRACE_INFERRED_PROXY_SERVICES_ENABLED has a deprecated alias, but the canonical
    # itself is not deprecated — it must NOT appear in DEPRECATED_CONFIGURATIONS.
    assert "DD_TRACE_INFERRED_PROXY_SERVICES_ENABLED" not in sc.DEPRECATED_CONFIGURATIONS


def test_deprecated_marker_only_entries_have_empty_dict():
    # Vars marked deprecated but with no metadata (e.g. DATADOG_TAGS) appear with {}.
    assert sc.DEPRECATED_CONFIGURATIONS.get("DATADOG_TAGS") == {}
