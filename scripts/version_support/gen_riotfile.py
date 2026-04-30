from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import datetime
import hashlib
import pprint
import shutil
from typing import Any

from riot import Venv

import riotfile


ROOT = Path(__file__).resolve().parents[2]
RIOTFILE_TEMPLATE_PATH = Path(ROOT / "scripts/version_support/version_support_riotfile.py")
RIOTFILE_OUTPUT_PATH = Path(ROOT / "version_support_riotfile.py")
GENERATED_VENVS_START = "# VERSION_SUPPORT_VENVS_START"
GENERATED_VENVS_END = "# VERSION_SUPPORT_VENVS_END"
PIPELINE_PATH = Path(ROOT / "version-support-pipeline.yml")
GITLAB_PATH = ROOT / ".gitlab"


def _format_pkg_value(value: Any) -> str:
    """Format a package version value for the generated riotfile."""
    if value == "":
        return "latest"
    if isinstance(value, list):
        return f"[{', '.join(_format_pkg_value(item) for item in value)}]"
    return pprint.pformat(value, width=120, sort_dicts=False)


def _format_python_versions(pys: list[str]) -> str:
    """Format Python version lists using compact riot helpers when possible."""
    if not pys:
        return "[]"
    if len(pys) == 1:
        return pprint.pformat(pys[0])
    if pys == riotfile.select_pys(min_version=pys[0]):
        return f'select_pys(min_version="{pys[0]}")'
    if pys == riotfile.select_pys(min_version=pys[0], max_version=pys[-1]):
        return f'select_pys(min_version="{pys[0]}", max_version="{pys[-1]}")'
    return pprint.pformat(pys, width=120, sort_dicts=False)


def _format_pkgs(pkgs: dict[str, Any], indent: int) -> str:
    """Format a package mapping with indentation for nested Venv definitions."""
    if not pkgs:
        return "{}"

    items = [f"{pprint.pformat(package)}: {_format_pkg_value(version)}" for package, version in pkgs.items()]
    if len(items) == 1:
        return f"{{{items[0]}}}"

    child_indent = " " * indent
    closing_indent = " " * (indent - 4)
    joined_items = ",\n".join(f"{child_indent}{item}" for item in items)
    return f"{{\n{joined_items},\n{closing_indent}}}"


def _format_value(value: Any, indent: int) -> str:
    """Format a generic value and preserve indentation across wrapped lines."""
    return pprint.pformat(value, width=120, sort_dicts=False).replace("\n", f"\n{' ' * indent}")


def _format_venv(venv: Venv, indent: int = 0) -> str:
    """Render a Venv and its children as Python source for the generated riotfile."""
    prefix = " " * indent
    lines = [f"{prefix}Venv("]
    for attr in ("name", "command"):
        value = getattr(venv, attr, None)
        if value:
            lines.append(f"{prefix}    {attr}={_format_value(value, indent + 4)},")
    pys = getattr(venv, "version_support_pys", None)
    if pys:
        lines.append(f"{prefix}    pys={_format_python_versions(pys)},")
    pkgs = getattr(venv, "pkgs", None)
    if pkgs:
        lines.append(f"{prefix}    pkgs={_format_pkgs(pkgs, indent + 8)},")
    env = getattr(venv, "env", None)
    if env:
        lines.append(f"{prefix}    env={_format_value(env, indent + 4)},")
    if venv.venvs:
        lines.append(f"{prefix}    venvs=[")
        for child in venv.venvs:
            lines.append(f"{_format_venv(child, indent + 8)},")
        lines.append(f"{prefix}    ],")
    lines.append(f"{prefix})")
    return "\n".join(lines)


def _format_venvs(venvs: list[Venv]) -> str:
    """Render all top-level Venv entries for insertion into the riotfile template."""
    if not venvs:
        return ""
    formatted = []
    for venv in venvs:
        formatted.append(f"{_format_venv(venv, 8)},")
    return "\n".join(formatted)


def _get_venv_python_versions(venv: Venv) -> list[str]:
    """Return the concrete Python versions configured on a Venv."""
    pys = getattr(venv, "version_support_pys", None)
    if pys is None:
        pys = getattr(venv, "pys", None) or []
    return [getattr(py, "_hint", py) for py in pys]


def _merge_base_child_defaults(base_venv: Venv, target: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Merge package and env defaults from matching base child Venvs."""
    matched_children = []
    matched_child_ids = set()
    for py in target["pys"]:
        for child in base_venv.venvs:
            if py not in _get_venv_python_versions(child):
                continue
            child_id = id(child)
            if child_id not in matched_child_ids:
                matched_children.append(child)
                matched_child_ids.add(child_id)
            break

    pkgs = {}
    env = {}
    for child in matched_children:
        pkgs.update(getattr(child, "pkgs", None) or {})
        env.update(getattr(child, "env", None) or {})

    pkgs[target["package"]] = target["version_to_test"]
    pkgs.update(target["pkgs"])

    target_env = target["env"] or {}
    env.update(target_env)
    return pkgs, env or None


def generate_new_riot_venvs(test_spec, base_venvs):
    """Attach generated version-support child Venvs to the matching base Venvs."""
    base_venvs_by_name = {venv.name: venv for venv in base_venvs}

    for integration_name, targets in test_spec.items():
        children = []
        base_venv = base_venvs_by_name[integration_name]
        for target in targets:
            pkgs, env = _merge_base_child_defaults(base_venv, target)
            kwargs = {
                "pys": target["pys"],
                "pkgs": pkgs,
            }
            if env:
                kwargs["env"] = env
            child = Venv(**kwargs)
            child.version_support_pys = target["pys"]  # type: ignore[attr-defined]
            children.append(child)

        base_venv.venvs = children

    return base_venvs


def write_version_support_riotfile(venvs: list[Venv]) -> None:
    """Write the generated version-support riotfile from the template and Venvs."""
    shutil.copyfile(RIOTFILE_TEMPLATE_PATH, RIOTFILE_OUTPUT_PATH)
    content = RIOTFILE_OUTPUT_PATH.read_text()
    start = content.index(GENERATED_VENVS_START)
    end = content.index(GENERATED_VENVS_END) + len(GENERATED_VENVS_END)
    start_line = content.rfind("\n", 0, start) + 1
    end_line = content.find("\n", end) + 1

    generated_venvs = _format_venvs(venvs)
    generated = f"{generated_venvs}\n" if generated_venvs else ""
    RIOTFILE_OUTPUT_PATH.write_text(f"{content[:start_line]}{generated}{content[end_line:]}")


def _testrunner_image_hash() -> str:
    """Return a short hash for the testrunner image used in cache keys."""
    image = None
    for line in (GITLAB_PATH / "testrunner.yml").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("TESTRUNNER_IMAGE:"):
            image = line.split(":", 1)[1].strip("\"' ")
            break
    if image is None:
        raise RuntimeError("Unable to find TESTRUNNER_IMAGE in .gitlab/testrunner.yml")
    return hashlib.sha256(image.encode()).hexdigest()[:16]


def _render_template(name: str, **params: object) -> str:
    """Render a GitLab template with generation-time parameters."""
    template_path = (GITLAB_PATH / "templates" / name).with_suffix(".yml")
    return template_path.read_text(encoding="utf-8").format(**params).strip()


def _sort_python_versions(pys: set[str]) -> list[str]:
    """Sort Python versions numerically."""
    return sorted(pys, key=riotfile.str_to_version)


def _collect_python_versions(test_spec: dict[str, list[dict[str, Any]]]) -> set[str]:
    """Collect Python versions requested by the normalized version-support spec."""
    pys = set()
    for targets in test_spec.values():
        for target in targets:
            pys.update(target["pys"])
    return pys


def _collect_integration_python_versions(test_spec: dict[str, list[dict[str, Any]]], integration: str) -> set[str]:
    """Collect Python versions requested for one integration."""
    return _collect_python_versions({integration: test_spec[integration]})


def _build_base_venvs_job(all_python_versions: set[str]) -> str:
    """Render the shared build-base-venvs template for version-support."""
    python_versions = ", ".join(f'"{py}"' for py in _sort_python_versions(all_python_versions))
    needs = "\n    - job: prepare_version_support_riot\n      artifacts: true"
    current_month = datetime.datetime.now().month
    image_hash = _testrunner_image_hash()
    cached_testrunner = _render_template(
        "cached-testrunner", testrunner_image_hash=image_hash, current_month=current_month
    )
    build_base_venvs = _render_template(
        "build-base-venvs",
        stage="build",
        needs=needs,
        python_versions=python_versions,
        unpin_dependencies="${UNPIN_DEPENDENCIES:-false}",
        nightly_build="${NIGHTLY_BUILD:-false}",
        riotfile_args="-f version_support_riotfile.py",
    )
    return f"{cached_testrunner}\n\n{build_base_venvs}"


def _needs_for_python_versions(pys: set[str]) -> str:
    """Render GitLab `needs` entries for the requested build-base-venvs matrix rows."""
    lines = [
        "    - job: prepare_version_support_riot",
        "      artifacts: true",
        "    - job: build_base_venvs",
        "      artifacts: true",
    ]
    if pys:
        lines.extend(
            [
                "      parallel:",
                "        matrix:",
            ]
        )
        for py in _sort_python_versions(pys):
            lines.append(f'          - PYTHON_VERSION: "{py}"')
    return "\n".join(lines)


def write_pipeline(suite_specs: list[str], test_spec: dict[str, list[dict[str, Any]]]) -> None:
    """Write the GitLab pipeline that builds venvs once and runs each generated version-support suite."""
    all_python_versions = _collect_python_versions(test_spec)
    pipeline = f"""\
stages:
  - prepare
  - build
  - test

include:
  - local: ".gitlab/services.yml"
  - local: ".gitlab/testrunner.yml"

variables:
  RIOT_RUN_CMD: riot -f version_support_riotfile.py -P -v run --exitfirst --pass-env -s

prepare_version_support_riot:
  extends: .testrunner
  stage: prepare
  needs: []
  script:
    - scripts/uv-run-script scripts/version_support/gen_pipeline.py
    - scripts/compile-and-prune-test-requirements "riot -f version_support_riotfile.py"
  artifacts:
    paths:
      - version_support_riotfile.py
      - .riot/requirements/

{_build_base_venvs_job(all_python_versions)}

.version_support_base_riot:
  extends: .testrunner
  stage: test
  parallel: 4
  services:
    - !reference [.services, ddagent]
  before_script:
    - !reference [.testrunner, before_script]
    - unset DD_SERVICE
    - unset DD_ENV
    - unset DD_TAGS
    - unset DD_TRACE_REMOVE_INTEGRATION_SERVICE_NAMES_ENABLED
  script:
    - |
      hashes=( $(riot -f version_support_riotfile.py list --hash-only "${{SUITE_NAME}}" \
        | sort \
        | ./.gitlab/ci-split-input.sh) )
      echo "NIGHTLY_BUILD: ${{NIGHTLY_BUILD}}"
      if [[ ${{#hashes[@]}} -eq 0 ]]; then
        echo "No riot hashes found for ${{SUITE_NAME}}"
        exit 1
      fi
      for hash in "${{hashes[@]}}"
      do
        echo "Running riot hash: ${{hash}}"
        riot -f version_support_riotfile.py list "${{hash}}"
        export _CI_DD_TAGS="test.configuration.riot_hash:${{hash}}"
        ${{RIOT_RUN_CMD}} "${{hash}}" -- --ddtrace
      done
"""
    for suite_spec in suite_specs:
        clean_name = suite_spec.split("::")[-1]
        python_versions = _collect_integration_python_versions(test_spec, clean_name)
        pipeline += f"""\

{clean_name}:
  extends: .version_support_base_riot
  needs:
{_needs_for_python_versions(python_versions)}
  variables:
    SUITE_NAME: {clean_name}
"""

    PIPELINE_PATH.write_text(pipeline)
