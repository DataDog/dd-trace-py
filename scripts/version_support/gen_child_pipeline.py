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
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import typing as t


ROOT = Path(__file__).resolve().parents[2]
GITLAB = ROOT / ".gitlab"
TESTS = ROOT / "tests"
BENCHMARKS = ROOT / "benchmarks"
SEARCH_ROOTS = ((TESTS, ""), (BENCHMARKS, "benchmarks"))

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


def _parse_yaml_scalar(value: str) -> object:
    scalar = value.strip()
    if not (scalar.startswith('"') or scalar.startswith("'")):
        scalar = re.sub(r"\s+#.*$", "", scalar).strip()
    if scalar in {"true", "false"}:
        return scalar == "true"
    if scalar.isdigit():
        return int(scalar)
    if (scalar.startswith('"') and scalar.endswith('"')) or (scalar.startswith("'") and scalar.endswith("'")):
        return scalar[1:-1]
    return scalar


def _yaml_single_quote(value: str) -> str:
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def _load_simple_suitespec_yaml(path: Path) -> dict[str, object]:
    """Load the limited YAML shape used by suitespec.yml without external deps."""

    # AIDEV-NOTE: version_support_generate runs before script dependencies are installed.
    # Keep this parser limited to the suitespec.yml subset instead of importing ruamel/PyYAML.
    lines: list[tuple[int, str, int]] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped == "---" or stripped.startswith("#"):
            continue
        lines.append((len(raw_line) - len(raw_line.lstrip(" ")), stripped, line_number))

    def parse_block(index: int, indent: int) -> tuple[object, int]:
        if index >= len(lines):
            return {}, index

        current_indent, current, _ = lines[index]
        if current_indent < indent:
            return {}, index

        if current.startswith("- "):
            list_values: list[object] = []
            while index < len(lines):
                line_indent, line, _ = lines[index]
                if line_indent != indent or not line.startswith("- "):
                    break
                item = line[2:].strip()
                index += 1
                if item:
                    list_values.append(_parse_yaml_scalar(item))
                else:
                    child_value, index = parse_block(index, indent + 2)
                    list_values.append(child_value)
            return list_values, index

        dict_values: dict[str, object] = {}
        while index < len(lines):
            line_indent, line, _ = lines[index]
            if line_indent != indent or line.startswith("- "):
                break
            if line.endswith(":"):
                key = line[:-1]
                value = ""
            elif ": " in line:
                key, value = line.split(": ", 1)
            else:
                raise ValueError(f"Unsupported suitespec line in {path}: {line!r}")
            index += 1
            if value.strip():
                dict_values[key] = _parse_yaml_scalar(value)
            elif index < len(lines) and lines[index][0] > line_indent:
                dict_values[key], index = parse_block(index, lines[index][0])
            else:
                dict_values[key] = {}
        return dict_values, index

    parsed, index = parse_block(0, 0)
    if index != len(lines) or not isinstance(parsed, dict):
        remaining = lines[index] if index < len(lines) else None
        raise ValueError(f"Unable to parse suitespec YAML file: {path} at {remaining!r}")
    return parsed


def _get_suites() -> dict[str, dict]:
    suitespec: dict[str, dict] = {"components": {}, "suites": {}}

    specfiles: list[tuple[Path, Path, str]] = []
    for root, ns_prefix in SEARCH_ROOTS:
        for specfile in root.rglob("suitespec.yml"):
            specfiles.append((specfile, root, ns_prefix))

    for specfile, root, ns_prefix in specfiles:
        path_parts = specfile.relative_to(root).parts[:-1]
        namespace = "::".join(path_parts) if path_parts else ns_prefix or None
        data = _load_simple_suitespec_yaml(specfile)

        components = data.get("components", {})
        if isinstance(components, dict):
            suitespec["components"].update(components)

        suites = data.get("suites", {})
        if not isinstance(suites, dict):
            continue
        if namespace is not None:
            for name, spec in list(suites.items()):
                if not isinstance(spec, dict):
                    continue
                spec.setdefault("pattern", name)
                suitespec["suites"][f"{namespace}::{name}"] = spec
        else:
            suitespec["suites"].update(suites)

    return suitespec["suites"]


def _collect_suite_venv_info(suite_patterns: dict[str, str], riot_venv: t.Any) -> dict[str, tuple[int, set[str]]]:
    compiled: dict[str, re.Pattern[str]] = {}
    for suite_name, pattern in suite_patterns.items():
        compiled[suite_name] = re.compile(pattern)

    venv_hashes: dict[str, set[str]] = {suite_name: set() for suite_name in compiled}
    python_versions: dict[str, set[str]] = {suite_name: set() for suite_name in compiled}

    for inst in riot_venv.instances():
        if not inst.name:
            continue

        hint = inst.py._hint  # type: ignore[attr-defined]
        for suite_name, regex in compiled.items():
            if inst.matches_pattern(regex):  # type: ignore[attr-defined]
                venv_hashes[suite_name].add(inst.short_hash)  # type: ignore[attr-defined]
                if re.match(r"^3\.\d+$", hint):
                    python_versions[suite_name].add(hint)

    return {
        suite_name: (len(venv_hashes[suite_name]), python_versions[suite_name])
        for suite_name in compiled
        if venv_hashes[suite_name]
    }


def _calculate_parallelism_from_venvs(venv_count: int, venvs_per_job: int, max_parallelism: int = 25) -> int:
    return min(math.ceil(venv_count / venvs_per_job), max_parallelism)


# ---------------------------------------------------------------------------
# Template / cache helpers
# ---------------------------------------------------------------------------


def _testrunner_image_hash() -> str:
    content = (GITLAB / "testrunner.yml").read_text(encoding="utf-8")
    match = re.search(r"^\s*TESTRUNNER_IMAGE:\s*(?P<image>.+?)\s*$", content, re.MULTILINE)
    if match is None:
        raise RuntimeError("Unable to find TESTRUNNER_IMAGE in .gitlab/testrunner.yml")
    image = match.group("image").strip("\"'")
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
    return f"\n{template_path.read_text().format(**params).strip()}\n"


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


def generate(output: Path) -> None:
    spec_data = _get_spec_data()
    spec_json = json.dumps(spec_data)

    from version_support.ci_specs import load_integration_names_from_json_text
    from version_support.ci_specs import resolve_suite_names_for_integrations

    all_suites = _get_suites()
    integration_names = load_integration_names_from_json_text(spec_json)
    suite_names = resolve_suite_names_for_integrations(integration_names, suites=all_suites)

    rewritten_source = _rewrite_riotfile(spec_data)
    riot_venv = _load_riot_venv(rewritten_source)

    image_hash = _testrunner_image_hash()
    current_month = datetime.datetime.now().month
    unpin = os.getenv("UNPIN_DEPENDENCIES", "false") or "false"
    nightly = os.getenv("NIGHTLY_BUILD", "false") or "false"

    suite_patterns: dict[str, str] = {}
    for suite_name in suite_names:
        suite_config = all_suites.get(suite_name, {})
        pattern = suite_config.get("pattern", suite_name.split("::")[-1])
        suite_patterns[suite_name] = pattern

    suite_venv_info = _collect_suite_venv_info(suite_patterns, riot_venv)

    all_python_versions: set[str] = set()
    suite_python_versions: dict[str, set[str]] = {}
    for suite_name in suite_names:
        py_versions = suite_venv_info.get(suite_name, (0, set()))[1]
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
        f"  VERSION_SUPPORT_SPEC_JSON: {_yaml_single_quote(spec_json)}",
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
        "      PYTHONPATH=scripts python -m version_support."
        'run_specific_integrations_versions --spec-json "${VERSION_SUPPORT_SPEC_JSON}"',
        "      scripts/compile-and-prune-test-requirements",
        "  artifacts:",
        "    paths:",
        "      - riotfile.py",
        "      - .riot/requirements/",
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
        static_parallelism: int | None = suite_config.get("parallelism")
        venvs_per_job: int | None = suite_config.get("venvs_per_job")

        py_versions = suite_python_versions.get(suite_name, set())
        suite_env_name = env.get("SUITE_NAME", pattern)
        pip_cache_key = _pip_cache_key(suite_env_name)
        job_parallelism = static_parallelism
        if job_parallelism is None and venvs_per_job is not None and suite_name in suite_venv_info:
            # AIDEV-NOTE: Version-support child jobs must preserve the same sharding
            # semantics as the main tests generator, or large integration suites collapse
            # onto a single node and exceed their current runtime envelope.
            venv_count, _ = suite_venv_info[suite_name]
            computed_parallelism = _calculate_parallelism_from_venvs(venv_count, venvs_per_job)
            if computed_parallelism > 1:
                job_parallelism = computed_parallelism

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
        if job_parallelism is not None:
            emit(f"  parallel: {job_parallelism}")
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
