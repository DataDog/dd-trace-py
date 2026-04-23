"""Generate a self-contained GitLab child pipeline YAML for version-support integration testing.

The generated pipeline:
  1. Rewrites riotfile.py for the requested integration versions (prepare stage).
  2. Builds virtual environments (build stage).
  3. Runs the test suites for the requested integrations (test stage).

Invoked by the ``version_support_generate`` job defined in .gitlab/version-support.yml.
Input is provided via environment variables:
  VERSION_SUPPORT_SPEC_JSON   -- raw JSON spec string
  VERSION_SUPPORT_SPEC_FILE   -- path to a JSON spec file

Run locally for debugging:
    VERSION_SUPPORT_SPEC_JSON='...' PYTHONPATH=scripts python -m version_support.gen_child_pipeline
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import typing as t
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GITLAB = ROOT / ".gitlab"

# Make scripts/ and project root importable (same as gen_gitlab_config.py)
for _p in (str(ROOT / "scripts"), str(ROOT), str(ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Spec loading
# ---------------------------------------------------------------------------


def _get_spec_data() -> dict:
    spec_json = os.environ.get("VERSION_SUPPORT_SPEC_JSON")
    spec_file = os.environ.get("VERSION_SUPPORT_SPEC_FILE")
    if spec_json:
        return json.loads(spec_json)
    if spec_file:
        return json.loads(Path(spec_file).read_text(encoding="utf-8"))
    raise RuntimeError("Neither VERSION_SUPPORT_SPEC_JSON nor VERSION_SUPPORT_SPEC_FILE is set")


# ---------------------------------------------------------------------------
# Riotfile helpers
# ---------------------------------------------------------------------------


def _rewrite_riotfile(spec_data: dict) -> str:
    from version_support import collect_venvs
    from version_support import regenerate_suite
    from version_support.json_specs import load_specs_from_json_text
    from version_support.models import FallbackSpec
    from version_support.models import IntegrationSpec

    riotfile_path = ROOT / "riotfile.py"
    source = riotfile_path.read_text(encoding="utf-8")
    suites = collect_venvs(source, str(riotfile_path))
    specs = load_specs_from_json_text(json.dumps(spec_data), suites=suites)

    for suite_name, spec in specs.items():
        if isinstance(spec, FallbackSpec):
            continue
        if not isinstance(spec, IntegrationSpec):
            continue
        suite = suites.get(suite_name)
        if suite is None:
            raise RuntimeError(f"Suite {suite_name!r} not found in riotfile.py")
        source = regenerate_suite(source, suite=suite, integration_spec=spec)
        suites = collect_venvs(source, str(riotfile_path))

    return source


def _load_riot_venv(source: str) -> t.Any:
    ns: dict[str, t.Any] = {
        "__file__": str(ROOT / "riotfile.py"),
        "__name__": "generated_riotfile",
    }
    exec(compile(source, str(ROOT / "riotfile.py"), "exec"), ns)  # noqa: S102
    if "venv" not in ns:
        raise RuntimeError("Could not find root `venv = Venv(...)` in riotfile.py")
    return ns["venv"]


def _collect_python_versions(pattern: str, riot_venv: t.Any) -> set[str]:
    compiled = re.compile(pattern)
    versions: set[str] = set()
    for inst in riot_venv.instances():
        if not inst.name:
            continue
        if inst.matches_pattern(compiled):  # type: ignore[attr-defined]
            hint = inst.py._hint  # type: ignore[attr-defined]
            if re.match(r"^3\.\d+$", hint):
                versions.add(hint)
    return versions


# ---------------------------------------------------------------------------
# Template / cache helpers
# ---------------------------------------------------------------------------


def _testrunner_image_hash() -> str:
    import ruamel.yaml  # noqa: PLC0415

    data = ruamel.yaml.YAML().load((GITLAB / "testrunner.yml").read_text())
    image: str = data["variables"]["TESTRUNNER_IMAGE"]
    return hashlib.sha256(image.encode()).hexdigest()[:16]


def _pip_cache_key(suite_pattern: str) -> str:
    """Return the pip cache key for a suite, matching what the main pipeline uses."""
    try:
        return (
            subprocess.check_output(
                [".gitlab/scripts/get-riot-pip-cache-key.sh", suite_pattern],
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except Exception:
        return hashlib.sha256(suite_pattern.encode()).hexdigest()[:16]


def _render_template(name: str, **params: object) -> str:
    template_path = (GITLAB / "templates" / name).with_suffix(".yml")
    return "\n" + template_path.read_text().format(**params).strip() + "\n"


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


def generate(output: Path) -> None:
    spec_data = _get_spec_data()

    from version_support.ci_specs import load_integration_names_from_json_text
    from version_support.ci_specs import resolve_suite_names_for_integrations

    import suitespec  # type: ignore[import-untyped]

    all_suites = suitespec.get_suites()
    integration_names = load_integration_names_from_json_text(json.dumps(spec_data))
    suite_names = resolve_suite_names_for_integrations(integration_names, suites=all_suites)

    rewritten_source = _rewrite_riotfile(spec_data)
    riot_venv = _load_riot_venv(rewritten_source)

    image_hash = _testrunner_image_hash()
    current_month = datetime.datetime.now().month
    unpin = os.getenv("UNPIN_DEPENDENCIES", "false") or "false"
    nightly = os.getenv("NIGHTLY_BUILD", "false") or "false"

    all_python_versions: set[str] = set()
    suite_python_versions: dict[str, set[str]] = {}
    for suite_name in suite_names:
        suite_config = all_suites.get(suite_name, {})
        pattern = suite_config.get("pattern", suite_name.split("::")[-1])
        py_versions = _collect_python_versions(pattern, riot_venv)
        suite_python_versions[suite_name] = py_versions
        all_python_versions.update(py_versions)

    if not all_python_versions:
        all_python_versions = {"3.12"}

    out: list[str] = []

    def emit(*lines: str) -> None:
        out.extend(lines)

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------
    emit(
        "# Auto-generated by scripts/version_support/gen_child_pipeline.py",
        "# Do not edit manually.",
        "",
        "include:",
        '  - local: ".gitlab/testrunner.yml"',
        '  - local: ".gitlab/services.yml"',
        "",
        "variables:",
        "  RIOT_RUN_CMD: riot -P -v run --exitfirst --pass-env -s",
        "",
        "stages:",
        "  - prepare",
        "  - build",
        "  - test",
        "",
    )

    # ------------------------------------------------------------------
    # prepare_version_support_riot
    # ------------------------------------------------------------------
    emit(
        "prepare_version_support_riot:",
        "  extends: .testrunner",
        "  stage: prepare",
        "  needs: []",
        "  script:",
        "    - |",
        "      set -e -u -o pipefail",
        "      mkdir -p .gitlab/version-support",
        '      spec_path=".gitlab/version-support/specs.json"',
        '      if [[ -n "${VERSION_SUPPORT_SPEC_JSON:-}" ]]; then',
        "        printf '%s' \"${VERSION_SUPPORT_SPEC_JSON}\" > \"${spec_path}\"",
        '      elif [[ -n "${VERSION_SUPPORT_SPEC_FILE:-}" ]]; then',
        '        cp "${VERSION_SUPPORT_SPEC_FILE}" "${spec_path}"',
        "      fi",
        '      PYTHONPATH=scripts python -m version_support.run_specific_integrations_versions --spec-file "${spec_path}"',
        "      scripts/compile-and-prune-test-requirements",
        "  artifacts:",
        "    paths:",
        "      - riotfile.py",
        "      - .riot/requirements/",
        "      - .gitlab/version-support/specs.json",
        "",
    )

    # ------------------------------------------------------------------
    # .cached_testrunner — rendered from the shared template so cache
    # keys stay in sync with the main pipeline.
    # ------------------------------------------------------------------
    emit(
        _render_template(
            "cached-testrunner",
            testrunner_image_hash=image_hash,
            current_month=current_month,
        ),
        "",
    )

    # ------------------------------------------------------------------
    # build_base_venvs — rendered from the shared template but with the
    # correct needs (prepare_version_support_riot) for this pipeline.
    # ------------------------------------------------------------------
    python_versions_str = ", ".join(f'"{v}"' for v in sorted(all_python_versions))
    raw_build_job = _render_template(
        "build-base-venvs",
        python_versions=python_versions_str,
        unpin_dependencies=unpin,
        nightly_build=nightly,
    )
    # The template has `stage: setup` and `needs: []`; override both for the child pipeline.
    raw_build_job = raw_build_job.replace("  stage: setup\n", "  stage: build\n", 1)
    raw_build_job = raw_build_job.replace(
        "  needs: []\n",
        "  needs:\n    - job: prepare_version_support_riot\n      artifacts: true\n",
        1,
    )
    emit(raw_build_job, "")

    # ------------------------------------------------------------------
    # Test jobs — one per requested suite
    # ------------------------------------------------------------------
    for suite_name in suite_names:
        suite_config = all_suites.get(suite_name, {})
        if suite_config.get("skip", False):
            continue

        clean_name = suite_name.split("::")[-1]
        pattern: str = suite_config.get("pattern", clean_name)
        services: list[str] = suite_config.get("services") or []
        env: dict[str, str] = dict(suite_config.get("env") or {})
        snapshot: bool = bool(suite_config.get("snapshot", False))

        py_versions = suite_python_versions.get(suite_name, set())
        suite_env_name = env.get("SUITE_NAME", pattern)
        pip_cache_key = _pip_cache_key(suite_env_name)

        emit(f"test/{clean_name}:")
        emit("  extends: .testrunner")
        emit("  stage: test")

        emit("  services:")
        emit("    - !reference [.services, ddagent]")
        if snapshot:
            emit("    - !reference [.services, testagent]")
        for svc in services:
            emit(f"    - !reference [.services, {svc}]")

        emit("  needs:")
        emit("    - job: prepare_version_support_riot")
        emit("      artifacts: true")
        if py_versions:
            emit("    - job: build_base_venvs")
            emit("      artifacts: true")
            emit("      parallel:")
            emit("        matrix:")
            for pv in sorted(py_versions):
                emit(f'          - PYTHON_VERSION: "{pv}"')
        else:
            emit("    - job: build_base_venvs")
            emit("      artifacts: true")

        wait_for = set(services)
        if snapshot:
            wait_for.add("testagent")

        emit("  before_script:")
        emit("    - !reference [.testrunner, before_script]")
        emit("    - unset DD_SERVICE")
        emit("    - unset DD_ENV")
        emit("    - unset DD_TAGS")
        emit("    - unset DD_TRACE_REMOVE_INTEGRATION_SERVICE_NAMES_ENABLED")
        if snapshot:
            emit('    - export DD_TRACE_AGENT_URL="http://testagent:9126"')
            emit('    - ln -s "${CI_PROJECT_DIR}" "/home/bits/project"')
        emit(f'    - export NIGHTLY_BUILD="{nightly}"')
        if wait_for:
            emit(f"    - riot -v run -s --pass-env wait -- {' '.join(sorted(wait_for))}")

        emit("  script:")
        emit("    - |")
        emit('      hashes=( $(.gitlab/scripts/get-riot-hashes.sh "${SUITE_NAME}") )')
        emit("      if [[ ${#hashes[@]} -eq 0 ]]; then")
        emit('        echo "No riot hashes found for ${SUITE_NAME}"')
        emit("        exit 1")
        emit("      fi")
        emit('      for hash in "${hashes[@]}"')
        emit("      do")
        emit('        echo "Running riot hash: ${hash}"')
        emit('        riot list "${hash}"')
        emit('        export _CI_DD_TAGS="test.configuration.riot_hash:${hash}"')
        emit('        ${RIOT_RUN_CMD} "${hash}" -- --ddtrace')
        emit("      done")

        emit("  cache:")
        emit(f"    key: v1-pip-${{PIP_CACHE_KEY}}-{image_hash}-cache")
        emit("    paths:")
        emit("      - .cache")

        emit("  variables:")
        emit(f"    SUITE_NAME: {suite_env_name}")
        emit("    PIP_CACHE_DIR: ${CI_PROJECT_DIR}/.cache/pip")
        emit(f"    PIP_CACHE_KEY: {pip_cache_key}")
        for key, value in env.items():
            if key != "SUITE_NAME":
                emit(f"    {key}: {value}")
        emit("")

    output.write_text("\n".join(out), encoding="utf-8")
    print(f"Generated {output} ({len(suite_names)} suite(s): {', '.join(suite_names)})")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=ROOT / "version-support-pipeline.yml",
        help="Output path for the generated pipeline YAML (default: <repo-root>/version-support-pipeline.yml)",
    )
    parsed = parser.parse_args()
    generate(parsed.output)
