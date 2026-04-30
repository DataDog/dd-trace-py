from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pprint
import shutil
from typing import Any

from riot import Venv
from ruamel.yaml import YAML  # pyright: ignore[reportMissingImports]

import riotfile


ROOT = Path(__file__).resolve().parents[2]
RIOTFILE_TEMPLATE_PATH = Path(ROOT / "scripts/version_support/version_support_riotfile.py")
RIOTFILE_OUTPUT_PATH = Path(ROOT / "version_support_riotfile.py")
GENERATED_VENVS_START = "# VERSION_SUPPORT_VENVS_START"
GENERATED_VENVS_END = "# VERSION_SUPPORT_VENVS_END"
PIPELINE_PATH = Path(ROOT / "version-support-pipeline.yml")


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


def generate_new_riot_venvs(test_spec, base_venvs):
    """Attach generated version-support child Venvs to the matching base Venvs."""
    base_venvs_by_name = {venv.name: venv for venv in base_venvs}

    for integration_name, targets in test_spec.items():
        children = []
        for target in targets:
            pkgs = {target["package"]: target["version_to_test"]}
            pkgs.update(target["pkgs"])
            kwargs = {
                "pys": target["pys"],
                "pkgs": pkgs,
            }
            if target["env"]:
                kwargs["env"] = target["env"]
            child = Venv(**kwargs)
            child.version_support_pys = target["pys"]  # type: ignore[attr-defined]
            children.append(child)

        base_venv = base_venvs_by_name[integration_name]
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


def write_pipeline(suite_specs: list[str]) -> None:
    """Write the GitLab pipeline that runs each generated version-support suite."""
    pipeline = {
        "stages": ["riot"],
        "include": [{"local": ".gitlab/tests.yml"}],
    }
    for suite_spec in suite_specs:
        clean_name = suite_spec.split("::")[-1]
        pipeline[f"version_support_{clean_name}"] = {
            "extends": ".test_base_riot",
            "variables": {
                "SUITE_NAME": clean_name,
            },
        }

    yaml = YAML()
    yaml.default_flow_style = False
    with PIPELINE_PATH.open("w") as f:
        yaml.dump(pipeline, f)
