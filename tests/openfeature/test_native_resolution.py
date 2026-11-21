"""Compatibility of native resolution metadata on successful and unsuccessful paths."""

import json

import pytest

from ddtrace.internal.native._native import ffe
from tests.openfeature.config_helpers import create_boolean_flag
from tests.openfeature.config_helpers import create_config


@pytest.mark.parametrize(
    "outcome",
    ["assigned", "independent_with_dependencies", "dependent", "disabled", "unassigned", "missing", "type_mismatch"],
)
def test_native_allocation_metadata_is_an_independent_read_only_property(outcome):
    flag = create_boolean_flag("root")
    allocation = "allocation-\N{VERTICAL TRAFFIC LIGHT}"
    flag["allocations"][0]["key"] = allocation
    flags = [flag]
    if outcome == "dependent":
        flag["allocations"][0]["rules"] = [
            {"conditions": [{"flagEvaluation": {"key": "parent"}, "operator": "ONE_OF", "value": ["true"]}]}
        ]
        flags.append(create_boolean_flag("parent"))
    elif outcome == "independent_with_dependencies":
        other = create_boolean_flag("other")
        other["allocations"][0]["rules"] = [
            {"conditions": [{"flagEvaluation": {"key": "root"}, "operator": "ONE_OF", "value": ["true"]}]}
        ]
        flags.append(other)
    elif outcome == "disabled":
        flag["enabled"] = False
    elif outcome == "unassigned":
        flag["allocations"] = []
    config = ffe.Configuration(json.dumps(create_config(*flags)).encode())
    flag_type = ffe.FlagType.String if outcome == "type_mismatch" else ffe.FlagType.Boolean

    details = config.resolve_value(
        "missing" if outcome == "missing" else "root",
        flag_type,
        {"targeting_key": "subject", "attributes": {}},
    )

    assigned = outcome in ("assigned", "independent_with_dependencies", "dependent")
    expected = {"allocation_key": allocation} if assigned else {}
    assert details.flag_metadata == expected
    assert details.allocation_key == (allocation if assigned else None)
    if assigned:
        assert details.value is True
        assert details.error_code is None
    if outcome == "dependent":
        assert [dependency.flag_key for dependency in details.dependency_evaluations] == ["parent"]
    else:
        assert details.dependency_evaluations == []
    metadata = details.flag_metadata
    metadata["allocation_key"] = "modified"
    assert details.flag_metadata == expected
    with pytest.raises(AttributeError):
        setattr(details, "flag_metadata", {})
