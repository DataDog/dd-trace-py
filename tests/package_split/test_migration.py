"""migration gate: coexistence, broken providers, duplicate plugins, rollback.

All artifacts are crafted wheels: an old distribution, a new provider, a
bridge that must only fall back when the new top-level package is genuinely
absent, and a deliberately broken new installation.
"""

from __future__ import annotations

from pathlib import Path

from package_split.evidence import FAIL
from package_split.evidence import PASS
from package_split.gates import migration

from ._support import Args
from ._support import craft_wheel
from ._support import make_report
from ._support import make_tree
from ._support import package_files


BRIDGE_SOURCE = """\
try:
    from newpkg.provider import Provider
except ModuleNotFoundError:
    # Fallback only for a genuinely absent new top-level package.
    from oldpkg.provider import Provider
"""


def build_wheels(tmp_path: Path):
    wheels = tmp_path / "wheels"

    oldpkg = craft_wheel(
        wheels,
        "oldpkg",
        "1.0.0",
        package_files("oldpkg", **{"__init__.py": "", "provider.py": "class Provider:\n    origin = 'old'\n"}),
        entry_points={"pytest11": {"old_plugin": "oldpkg:plugin"}},
    )
    newpkg = craft_wheel(
        wheels,
        "newpkg",
        "1.0.0",
        package_files("newpkg", **{"__init__.py": "", "provider.py": "class Provider:\n    origin = 'new'\n"}),
    )
    bridge = craft_wheel(
        wheels,
        "bridge",
        "1.0.0",
        package_files("bridge", **{"__init__.py": BRIDGE_SOURCE}),
        # Unversioned on purpose: the broken-provider scenario installs a
        # deliberately older newpkg and must not have it upgraded away.
        requires=["newpkg", "oldpkg"],
    )
    brokennew = craft_wheel(
        wheels,
        "newpkg",
        "0.9.0",
        package_files(
            "newpkg",
            **{
                "__init__.py": (
                    # Simulates a broken installation: a dependency of the new\n"
                    # provider fails to import with ModuleNotFoundError, which a\n"
                    # sloppy bridge would silently swallow.\n
                    "raise ModuleNotFoundError('newpkg is broken: dependency missing')\n"
                ),
                "provider.py": "class Provider:\n    origin = 'broken'\n",
            },
        ),
    )
    return oldpkg, newpkg, bridge, brokennew


def run_migration(tmp_path: Path, matrix_text: str, candidate=None):
    from package_split.manifest import load_manifest

    matrix = tmp_path / "matrix.toml"
    matrix.write_text(matrix_text, encoding="utf-8")
    manifest_path = make_tree(tmp_path / "src", "sample", {"__init__.py": ""})
    manifest = load_manifest(manifest_path)
    report = make_report("migration", manifest_path)
    migration.run_gate(manifest, Args(matrix=matrix, candidate=candidate), report)
    return report


def scenario_by_name(report, name):
    matches = [c for c in report.checks if c.name == "scenario:%s" % name]
    assert matches, "scenario %r not found in %r" % (name, [c.name for c in report.checks])
    return matches[0]


def test_coexistence_uses_new_provider(tmp_path):
    oldpkg, newpkg, bridge, _ = build_wheels(tmp_path)
    report = run_migration(
        tmp_path,
        """\
schema_version = 1

[[scenario]]
name = "coexistence"

[[scenario.step]]
action = "install"
artifact = "%(oldpkg)s"

[[scenario.step]]
action = "install"
artifact = "%(newpkg)s"

[[scenario.step]]
action = "install"
artifact = "%(bridge)s"

[scenario.checks]
file_ownership = true
imports_ok = ["bridge", "newpkg", "oldpkg"]

[scenario.checks.identity]
bridge = "bridge:Provider"
expect_module = "newpkg.provider"

[scenario.checks.entry_points]
group = "pytest11"
count = 1
"""
        % {"oldpkg": oldpkg, "newpkg": newpkg, "bridge": bridge},
    )
    assert report.status == PASS, report.summary()
    assert scenario_by_name(report, "coexistence").status == PASS


def test_broken_new_provider_must_not_fall_back(tmp_path):
    # The plan's broken-provider negative fixture: with a broken new package
    # installed, the bridge must fail loudly. A silent fallback to oldpkg
    # resolves the identity to the wrong module and fails the gate.
    oldpkg, newpkg, bridge, brokennew = build_wheels(tmp_path)
    report = run_migration(
        tmp_path,
        """\
schema_version = 1

[[scenario]]
name = "broken-new"

[[scenario.step]]
action = "install"
artifact = "%(oldpkg)s"

[[scenario.step]]
action = "install"
artifact = "%(brokennew)s"

[[scenario.step]]
action = "install"
artifact = "%(bridge)s"

[scenario.checks.identity]
bridge = "bridge:Provider"
expect_module = "newpkg.provider"
"""
        % {"oldpkg": oldpkg, "brokennew": brokennew, "bridge": bridge},
    )
    assert report.status == FAIL
    scenario = scenario_by_name(report, "broken-new")
    assert scenario.status == FAIL
    assert "resolved to" in scenario.detail


def test_duplicate_plugin_registration_fails(tmp_path):
    # Exactly one effective plugin: if both old and new distributions
    # register the same entry-point group, the gate must reject it.
    oldpkg, newpkg, bridge, _ = build_wheels(tmp_path)
    newpkg_both = craft_wheel(
        tmp_path / "wheels",
        "newpkg",
        "1.1.0",
        package_files("newpkg", **{"__init__.py": "", "provider.py": "class Provider:\n    origin = 'new'\n"}),
        entry_points={"pytest11": {"new_plugin": "newpkg:plugin"}},
    )
    report = run_migration(
        tmp_path,
        """\
schema_version = 1

[[scenario]]
name = "duplicate-plugin"

[[scenario.step]]
action = "install"
artifact = "%(oldpkg)s"

[[scenario.step]]
action = "install"
artifact = "%(newpkg)s"

[[scenario.step]]
action = "install"
artifact = "%(bridge)s"

[scenario.checks.identity]
bridge = "bridge:Provider"
expect_module = "newpkg.provider"

[scenario.checks.entry_points]
group = "pytest11"
count = 1
"""
        % {"oldpkg": oldpkg, "newpkg": newpkg_both, "bridge": bridge},
    )
    assert report.status == FAIL
    scenario = scenario_by_name(report, "duplicate-plugin")
    assert "entry point group" in scenario.detail


def test_overlapping_file_ownership_fails(tmp_path):
    oldpkg, newpkg, _, _ = build_wheels(tmp_path)
    claimer = craft_wheel(
        tmp_path / "wheels",
        "claimant",
        "1.0.0",
        package_files("oldpkg", **{"__init__.py": "", "provider.py": "class Provider:\n    origin = 'duplicate'\n"}),
    )
    report = run_migration(
        tmp_path,
        """\
schema_version = 1

[[scenario]]
name = "ownership-conflict"

[[scenario.step]]
action = "install"
artifact = "%(oldpkg)s"

[[scenario.step]]
action = "install"
artifact = "%(claimant)s"

[scenario.checks]
file_ownership = true
imports_ok = ["oldpkg"]
"""
        % {"oldpkg": oldpkg, "claimant": claimer},
    )
    assert report.status == FAIL
    scenario = scenario_by_name(report, "ownership-conflict")
    assert "overlapping file ownership" in scenario.detail


def test_rollback_to_old_provider(tmp_path):
    # Removing the new package must leave the bridge working against the old
    # provider, and the new namespace must be genuinely absent (import
    # fails), proving shims refer only to installed implementations.
    oldpkg, newpkg, bridge, _ = build_wheels(tmp_path)
    report = run_migration(
        tmp_path,
        """\
schema_version = 1

[[scenario]]
name = "rollback"

[[scenario.step]]
action = "install"
artifact = "%(oldpkg)s"

[[scenario.step]]
action = "install"
artifact = "%(newpkg)s"

[[scenario.step]]
action = "install"
artifact = "%(bridge)s"

[[scenario.step]]
action = "uninstall"
distribution = "newpkg"

[scenario.checks]
imports_fail = ["newpkg"]

[scenario.checks.identity]
bridge = "bridge:Provider"
expect_module = "oldpkg.provider"
"""
        % {"oldpkg": oldpkg, "newpkg": newpkg, "bridge": bridge},
    )
    assert report.status == PASS, report.summary()
    assert scenario_by_name(report, "rollback").status == PASS
