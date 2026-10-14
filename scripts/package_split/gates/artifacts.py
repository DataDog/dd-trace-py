"""artifacts gate: isolated builds, sdist rebuild parity and install checks.

Pass criteria (docs/package-split/plan.md):

- wheel and sdist build successfully in an isolated (PEP 517) environment;
- the wheel can be rebuilt from the sdist with identical package contents;
- the wheel installs into a fresh virtual environment created outside the
  checkout, with no editable installs, no source-path leakage, metadata,
  entry points and declared resources present and consistent;
- for ``native-free`` packages: no native files in the wheel and no native
  artifacts anywhere in the resolved runtime dependency graph.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import Any

from .. import wheelhouse
from ..evidence import FAIL
from ..evidence import NOT_RUN
from ..evidence import PASS
from ..manifest import Manifest
from ..venvutil import create_venv
from ..venvutil import installed_distributions
from ..venvutil import pip_install
from ..venvutil import run_in_venv
from ..wheelhouse import BuildError


def run_gate(manifest: Manifest, args: Any, report: Any) -> None:
    workdir = Path(tempfile.mkdtemp(prefix="package-split-artifacts-"))
    _run_gate(manifest, args, report, workdir)


def _run_gate(manifest: Manifest, args: Any, report: Any, workdir: Path) -> None:
    build_dir = workdir / "build"

    # 1. Isolated build of sdist + wheel.
    try:
        artifacts = wheelhouse.build_distributions(manifest.root, build_dir, build_command=manifest.build_command)
    except BuildError as exc:
        report.record("isolated-build", FAIL, str(exc)[:2000])
        return
    report.record(
        "isolated-build",
        PASS,
        "built %s and %s" % (artifacts["sdist"].name, artifacts["wheel"].name),
        sdist={"name": artifacts["sdist"].name, "sha256": wheelhouse.sha256_file(artifacts["sdist"])},
        wheel={"name": artifacts["wheel"].name, "sha256": wheelhouse.sha256_file(artifacts["wheel"])},
    )

    wheel = wheelhouse.read_wheel(artifacts["wheel"])

    # 2. Native-free wheel contents.
    native_files = wheelhouse.wheel_native_files(wheel)
    if manifest.native_policy == "native-free":
        if native_files:
            report.record(
                "wheel-native-free",
                FAIL,
                "native-free wheel contains native files: %s" % native_files[:20],
                native_files=native_files,
            )
        else:
            report.record("wheel-native-free", PASS, "wheel contains no native files")
    else:
        report.record(
            "wheel-native-free",
            PASS,
            "native policy %s permits native content (%d native files)" % (manifest.native_policy, len(native_files)),
            native_files=native_files[:20],
        )

    # 3. Rebuild the wheel from the sdist and compare package contents.
    _rebuild_check(manifest, report, artifacts, workdir)

    # 4. Fresh-venv install checks outside the checkout.
    _install_checks(manifest, report, artifacts, workdir)

    # 5. Dependency metadata consistency.
    _metadata_consistency(manifest, report, wheel)


def _rebuild_check(manifest: Manifest, report: Any, artifacts: dict[str, Path], workdir: Path) -> None:
    rebuild_dir = workdir / "rebuild"
    try:
        source_root = wheelhouse.unpack_sdist(artifacts["sdist"], rebuild_dir / "sdist")
        rebuilt = wheelhouse.build_distributions(source_root, rebuild_dir / "dist")
    except BuildError as exc:
        report.record("sdist-rebuild", FAIL, "rebuilding from the sdist failed: %s" % str(exc)[:2000])
        return

    rebuilt_wheel = wheelhouse.read_wheel(rebuilt["wheel"])
    original_wheel = wheelhouse.read_wheel(artifacts["wheel"])
    if rebuilt_wheel.name != original_wheel.name or rebuilt_wheel.version != original_wheel.version:
        report.record(
            "sdist-rebuild",
            FAIL,
            "rebuilt wheel is %s-%s, original is %s-%s"
            % (rebuilt_wheel.name, rebuilt_wheel.version, original_wheel.name, original_wheel.version),
        )
        return

    original_files = set(original_wheel.package_files())
    rebuilt_files = set(rebuilt_wheel.package_files())
    missing = sorted(original_files - rebuilt_files)
    extra = sorted(rebuilt_files - original_files)
    if missing or extra:
        report.record(
            "sdist-rebuild",
            FAIL,
            "package contents differ when rebuilt from the sdist",
            missing_from_rebuild=missing[:100],
            extra_in_rebuild=extra[:100],
        )
        return
    report.record(
        "sdist-rebuild",
        PASS,
        "wheel rebuilt from the sdist with identical package contents (%d files)" % len(original_files),
    )


def _install_checks(manifest: Manifest, report: Any, artifacts: dict[str, Path], workdir: Path) -> None:
    # The venv lives outside both the checkout and the artifact directory so
    # a wheel missing files cannot accidentally resolve from the source tree.
    venv_dir = workdir / "venv"
    try:
        create_venv(venv_dir)
    except Exception as exc:
        report.record("fresh-install", NOT_RUN, "venv creation failed: %s" % exc)
        return
    try:
        pip_install(
            venv_dir,
            [str(artifacts["wheel"])],
            find_links=[artifacts["wheel"].parent],
            no_index=True,
        )
    except Exception as exc:
        report.record("fresh-install", FAIL, "wheel install failed: %s" % str(exc)[:2000])
        return

    installed = installed_distributions(venv_dir)
    if manifest.distribution.lower() not in {name.lower() for name in installed}:
        report.record(
            "fresh-install",
            FAIL,
            "distribution %r not present after install (installed: %r)" % (manifest.distribution, sorted(installed)),
        )
        return
    report.record(
        "fresh-install",
        PASS,
        "installed %s from wheel into a fresh venv" % manifest.distribution,
        resolved_versions=installed,
    )

    # Import from outside the checkout: no source-path leakage, no .pth files
    # pointing back at the repository, module resolves under site-packages.
    root_module = manifest.import_roots[0]
    probe_code = (
        "import importlib, json, sys, pathlib\n"
        # ``python -c`` puts the cwd on sys.path; scrub it (and any empty
        # entry) so modules can only resolve from the venv, never the
        # source checkout the gate happens to run from.
        "_cwd = str(pathlib.Path.cwd())\n"
        "sys.path = [p for p in sys.path if p not in ('', _cwd)]\n"
        "mods = %r\n"
        "out = {'modules': {}}\n"
        "for m in mods:\n"
        "    try:\n"
        "        mod = importlib.import_module(m)\n"
        "        out['modules'][m] = {'ok': True, 'file': getattr(mod, '__file__', None)}\n"
        "    except Exception as exc:\n"
        "        out['modules'][m] = {'ok': False, 'error': repr(exc)}\n"
        "import sysconfig\n"
        "site = sysconfig.get_paths()['purelib']\n"
        "out['leaks'] = [p for p in sys.path if pathlib.Path(p) == pathlib.Path(%r)]\n"
        "out['editable'] = [p for p in sys.path if 'editable' in p.lower()]\n"
        "out['not_site'] = {m: v['file'] for m, v in out['modules'].items()\n"
        "                   if v.get('ok') and v.get('file') and not v['file'].startswith(site)}\n"
        "print(json.dumps(out))\n"
    ) % ([root_module] + list(manifest.supported_exports.keys()), str(manifest.root))
    result = run_in_venv(venv_dir, ["-c", probe_code], cwd=workdir)
    if result.returncode != 0:
        report.record(
            "import-outside-checkout",
            FAIL,
            "import probe failed: %s" % (result.stderr or result.stdout)[-2000:],
        )
        return

    probe = json.loads(result.stdout.strip().splitlines()[-1])
    problems = []
    for module, status in probe["modules"].items():
        if not status.get("ok"):
            problems.append("cannot import %s: %s" % (module, status.get("error")))
    if probe["leaks"]:
        problems.append("source checkout on sys.path: %r" % probe["leaks"])
    if probe["editable"]:
        problems.append("editable-install artifacts on sys.path: %r" % probe["editable"])
    if probe["not_site"]:
        problems.append("modules resolved outside site-packages: %r" % probe["not_site"])
    if problems:
        report.record("import-outside-checkout", FAIL, "; ".join(problems), probe=probe)
    else:
        report.record("import-outside-checkout", PASS, "imports resolve from site-packages only", probe=probe)

    # Entry points declared in the manifest must exist in the installed dist.
    if manifest.entry_point_groups:
        missing = []
        for group, expected in manifest.entry_point_groups.items():
            for name in expected:
                probe_ep = (
                    "from importlib.metadata import entry_points\n"
                    "eps = [ep for ep in entry_points(group=%r)]\n"
                    "names = [ep.name for ep in eps]\n"
                    "print(0 if %r in names else 1)\n"
                ) % (group, name)
                result = run_in_venv(venv_dir, ["-c", probe_ep], cwd=workdir)
                if result.returncode != 0 or result.stdout.strip() == "1":
                    missing.append("%s:%s" % (group, name))
        if missing:
            report.record("entry-points", FAIL, "missing entry points: %s" % missing)
        else:
            report.record("entry-points", PASS, "all declared entry points present")

    # Declared resources must be present in the installed distribution.
    if manifest.resources:
        missing_resources = []
        for resource in manifest.resources:
            module_name, _, filename = resource.partition(":")
            probe_resource = (
                "import importlib.resources, sys, pathlib\n"
                # Scrub the cwd: ``python -c`` puts it on sys.path and the
                # source tree must not satisfy resource lookups.
                "_cwd = str(pathlib.Path.cwd())\n"
                "sys.path = [p for p in sys.path if p not in ('', _cwd)]\n"
                "try:\n"
                "    present = importlib.resources.files(%r).joinpath(%r).is_file()\n"
                "except Exception:\n"
                "    present = False\n"
                "print(0 if present else 1)\n"
            ) % (module_name, filename)
            result = run_in_venv(venv_dir, ["-c", probe_resource], cwd=workdir)
            if result.returncode != 0 or result.stdout.strip() == "1":
                missing_resources.append(resource)
        if missing_resources:
            report.record("resources", FAIL, "missing package resources: %s" % missing_resources)
        else:
            report.record("resources", PASS, "all declared resources present")

    # Native-free: scan the whole resolved runtime graph, not just our wheel.
    if manifest.native_policy == "native-free":
        native = wheelhouse.venv_native_files(venv_dir, installed)
        if native:
            report.record(
                "resolved-graph-native-free",
                FAIL,
                "native artifacts in the resolved runtime graph: %s" % native[:20],
                native_files=native,
            )
        else:
            report.record(
                "resolved-graph-native-free",
                PASS,
                "no native artifacts among %d installed distributions" % len(installed),
            )


def _metadata_consistency(manifest: Manifest, report: Any, wheel: Any) -> None:
    problems: list[str] = []
    requires = [r for r in wheel.metadata.get("requires-dist", [])]
    declared = set(manifest.runtime_dependencies)
    for requirement in requires:
        name = requirement.split(";")[0].split(" ")[0].split("[")[0].strip().lower().replace("_", "-")
        if name and name not in declared:
            problems.append("wheel requires %r which is not declared in [dependencies].runtime" % name)
    if problems:
        report.record("dependency-consistency", FAIL, "; ".join(problems), requires_dist=requires)
    else:
        report.record(
            "dependency-consistency",
            PASS,
            "wheel runtime requirements are declared in the manifest",
        )
