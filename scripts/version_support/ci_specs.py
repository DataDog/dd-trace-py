from __future__ import annotations

import json
from pathlib import Path


def load_integration_names_from_json_text(spec_json: str) -> list[str]:
    raw_data = json.loads(spec_json)
    raw_integrations = raw_data.get("integrations") if isinstance(raw_data, dict) else None
    if not isinstance(raw_integrations, list):
        raise ValueError("spec JSON must contain an 'integrations' array")

    names: list[str] = []
    for raw_item in raw_integrations:
        if not isinstance(raw_item, dict):
            raise ValueError(f"integration entry must be an object: {raw_item!r}")
        name = raw_item.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"integration entry requires a non-empty 'name': {raw_item!r}")
        names.append(name)
    return names


def load_integration_names_from_spec_inputs(
    *,
    spec_file: str | Path | None = None,
    spec_json: str | None = None,
) -> list[str]:
    if spec_json:
        return load_integration_names_from_json_text(spec_json)
    if spec_file:
        raw_json = Path(spec_file).read_text(encoding="utf-8")
        return load_integration_names_from_json_text(raw_json)
    return []


def resolve_suite_names_for_integrations(integration_names: list[str], suites: dict[str, dict]) -> list[str]:
    resolved: list[str] = []
    seen: set[str] = set()
    for integration_name in integration_names:
        if integration_name in suites:
            target_suite = integration_name
        else:
            candidates = sorted(suite_name for suite_name in suites if suite_name.endswith(f"::{integration_name}"))
            if not candidates:
                raise ValueError(
                    f"Unable to resolve integration {integration_name!r} to a suite key in tests/suitespec.yml"
                )
            if len(candidates) > 1:
                raise ValueError(f"Ambiguous suite mapping for integration {integration_name!r}: {candidates!r}")
            target_suite = candidates[0]
        if target_suite in seen:
            continue
        seen.add(target_suite)
        resolved.append(target_suite)
    return resolved
