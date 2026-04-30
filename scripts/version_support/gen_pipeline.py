#!/usr/bin/env scripts/uv-run-script
# -*- mode: python -*-
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "riot==0.21.0",
#     "ruamel.yaml==0.18.6",
# ]
# ///

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import json
import os

from riot import Venv
from ruamel.yaml import YAML

import riotfile
from scripts.version_support.gen_riotfile import generate_new_riot_venvs
from scripts.version_support.gen_riotfile import write_pipeline
from scripts.version_support.gen_riotfile import write_version_support_riotfile
from scripts.version_support.validate import validate_test_spec


def get_suites() -> dict[str, dict]:
    """Collect suites from `tests/**/suitespec.yml`, prefixing nested suites by path."""
    suitespec: dict[str, dict] = {}
    test_path = Path(__file__).resolve().parents[2] / "tests"

    for specfile in test_path.rglob("suitespec.yml"):
        path_parts = specfile.relative_to(test_path).parts[:-1]
        namespace = "::".join(path_parts) or None

        data = YAML(typ="safe").load(specfile.read_text(encoding="utf-8")) or {}
        if not data or not isinstance(data, dict):
            continue

        suites = data.get("suites", {})
        if not isinstance(suites, dict):
            continue

        if namespace is not None:
            for name, spec in list(suites.items()):
                if not isinstance(spec, dict):
                    continue
                spec.setdefault("pattern", name)
                suitespec[f"{namespace}::{name}"] = spec
            continue

        suitespec.update(suites)

    return suitespec


def get_riot_venvs(integration_names: set[str]):
    def iter_venvs(venv: Venv):
        yield venv
        for child_venv in venv.venvs:
            yield from iter_venvs(child_venv)

    return [venv for venv in iter_venvs(getattr(riotfile, "venv", Venv())) if venv.name in integration_names]


def generate_pipeline():
    test_spec_raw = os.environ.get("VERSION_SUPPORT_SPEC_JSON")
    if not test_spec_raw:
        raise RuntimeError("No spec was provided, stopping CI....")
    test_spec_raw = json.loads(test_spec_raw)

    # If the test_spec_json is malformed, it will crash at this point.
    # It allows to ensure we can iterate on the right format
    test_spec = validate_test_spec(test_spec_raw)

    # get all suitespec.yml under dd-trace-py/tests
    all_suite_specs = get_suites()
    target_integrations = set(test_spec.keys())

    # suites we want to run
    target_suite_specs = []
    for integration in target_integrations:
        for suite_spec in all_suite_specs:
            if integration == suite_spec.split("::")[-1]:
                target_suite_specs.append(suite_spec)

    base_venvs = get_riot_venvs(target_integrations)
    updated_venvs = generate_new_riot_venvs(test_spec, base_venvs)
    write_version_support_riotfile(updated_venvs)
    write_pipeline(target_suite_specs, test_spec)


if __name__ == "__main__":
    generate_pipeline()
