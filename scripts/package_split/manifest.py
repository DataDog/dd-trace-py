"""package-contract.toml: load and validate the versioned package contract.

The manifest is the machine-readable half of docs/package-split/plan.md
Deliverable 1. Validation is strict: unknown tables/keys, missing required
fields, wrong types and out-of-range values are all reported so that a
manifest cannot drift silently. Loaders reject unknown schema versions, so a
manifest written for a future harness is never silently misread.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any
from typing import Optional

from . import MANIFEST_SCHEMA_VERSION
from ._toml import tomllib


NATIVE_POLICIES = ("native-free", "native-optional", "native-required")

# Module-prefix style names: dotted Python identifiers.
_PREFIX_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


class ManifestError(Exception):
    """Raised when a manifest fails validation; .issues lists everything."""

    def __init__(self, path: Path, issues: list[str]) -> None:
        self.path = path
        self.issues = issues
        super().__init__("invalid manifest %s:\n  - %s" % (path, "\n  - ".join(issues)))


class Manifest:
    """Validated view over package-contract.toml contents."""

    def __init__(self, path: Path, data: dict[str, Any]) -> None:
        self.path = path
        self.root = path.parent
        self.data = data

    # -- identity ---------------------------------------------------------
    @property
    def distribution(self) -> str:
        return str(self.data["identity"]["distribution"])

    @property
    def import_roots(self) -> list[str]:
        return list(self.data["identity"]["import_roots"])

    @property
    def owner(self) -> str:
        return str(self.data["identity"]["owner"])

    @property
    def source_paths(self) -> list[Path]:
        return [self.root / p for p in self.data["identity"]["source_paths"]]

    @property
    def destination_repository(self) -> str:
        return str(self.data["identity"]["destination_repository"])

    # -- dependencies -----------------------------------------------------
    @property
    def native_policy(self) -> str:
        return str(self.data["dependencies"]["native_policy"])

    @property
    def runtime_dependencies(self) -> list[str]:
        return list(self.data["dependencies"]["runtime"])

    @property
    def extras(self) -> dict[str, list[str]]:
        return {name: list(deps) for name, deps in self.data["dependencies"].get("extras", {}).items()}

    @property
    def build_dependencies(self) -> list[str]:
        return list(self.data["dependencies"].get("build", []))

    @property
    def forbidden_namespaces(self) -> list[str]:
        return list(self.data["dependencies"].get("forbidden_namespaces", []))

    @property
    def allowed_edges(self) -> list[dict[str, Any]]:
        return list(self.data["dependencies"].get("allowed_edges", []))

    # -- compatibility ------------------------------------------------------
    @property
    def dependency_ranges(self) -> dict[str, str]:
        return dict(self.data["compatibility"]["dependency_ranges"])

    # -- contracts ----------------------------------------------------------
    @property
    def supported_exports(self) -> dict[str, list[str]]:
        return dict(self.data["contracts"]["supported_exports"])

    @property
    def behavioral_fixtures(self) -> list[list[str]]:
        return [list(c) for c in self.data["contracts"].get("behavioral_fixtures", [])]

    @property
    def activation_api(self) -> Optional[dict[str, str]]:
        api = self.data["contracts"].get("activation_api")
        return dict(api) if api else None

    # -- release ------------------------------------------------------------
    @property
    def build_command(self) -> Optional[list[str]]:
        command = self.data["release"].get("build_command")
        return list(command) if command else None

    @property
    def entry_point_groups(self) -> dict[str, list[str]]:
        return dict(self.data["release"].get("entry_points", {}))

    @property
    def resources(self) -> list[str]:
        return list(self.data["release"].get("resources", []))


def _type_of(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "table"
    return type(value).__name__


def _check_table(issues: list[str], data: dict[str, Any], table: str, fields: dict[str, str]) -> None:
    actual = data.get(table, {})
    if not isinstance(actual, dict):
        issues.append("[%s]: expected a table, got %s" % (table, _type_of(actual)))
        return
    for key, expected in fields.items():
        optional = expected.startswith("?")
        expected_type = expected[1:] if optional else expected
        if key not in actual:
            if not optional:
                issues.append("[%s]: missing required field %r" % (table, key))
            continue
        value = actual[key]
        if _type_of(value) != expected_type:
            issues.append("[%s].%s: expected %s, got %s" % (table, key, expected_type, _type_of(value)))
    unknown = set(actual) - set(fields)
    for key in sorted(unknown):
        issues.append("[%s]: unknown field %r" % (table, key))


def _validate_str_list(issues: list[str], data: Any, where: str, require_nonempty: bool = True) -> None:
    if not isinstance(data, list) or not all(isinstance(v, str) for v in data):
        issues.append("%s: expected a list of strings" % where)
        return
    if require_nonempty and not data:
        issues.append("%s: must not be empty" % where)


def validate_manifest(data: Any) -> list[str]:
    """Return the list of validation issues (empty when valid)."""
    issues: list[str] = []

    if not isinstance(data, dict):
        return ["manifest: expected a TOML document at the top level"]

    unknown_top = set(data) - {
        "schema_version",
        "identity",
        "dependencies",
        "compatibility",
        "contracts",
        "validation",
        "release",
    }
    for key in sorted(unknown_top):
        issues.append("unknown top-level table %r" % key)

    version = data.get("schema_version")
    if version is None:
        issues.append("missing required field 'schema_version'")
    elif not isinstance(version, int) or isinstance(version, bool):
        issues.append("'schema_version': expected an integer")
    elif version != MANIFEST_SCHEMA_VERSION:
        issues.append(
            "'schema_version': %r is not supported by this harness (expected %d)" % (version, MANIFEST_SCHEMA_VERSION)
        )

    _check_table(
        issues,
        data,
        "identity",
        {
            "distribution": "string",
            "import_roots": "list",
            "owner": "string",
            "source_paths": "list",
            "destination_repository": "string",
        },
    )
    identity = data.get("identity", {})
    if isinstance(identity, dict):
        for field in ("distribution", "owner", "destination_repository"):
            value = identity.get(field)
            if isinstance(value, str) and not value.strip():
                issues.append("[identity].%s: must not be empty" % field)
        roots = identity.get("import_roots")
        if isinstance(roots, list) and roots:
            for root in roots:
                if not isinstance(root, str) or not _PREFIX_RE.match(root):
                    issues.append("[identity].import_roots: invalid module prefix %r" % (root,))
        else:
            issues.append("[identity].import_roots: at least one import root is required")
        _validate_str_list(issues, identity.get("source_paths"), "[identity].source_paths")

    _check_table(
        issues,
        data,
        "dependencies",
        {
            "runtime": "list",
            "build": "?list",
            "extras": "?table",
            "native_policy": "string",
            "forbidden_namespaces": "?list",
            "allowed_edges": "?list",
        },
    )
    dependencies = data.get("dependencies", {})
    if isinstance(dependencies, dict):
        # Runtime dependencies may legitimately be empty (a pure-python
        # package with no dependencies); the field itself is still required.
        _validate_str_list(issues, dependencies.get("runtime"), "[dependencies].runtime", require_nonempty=False)
        policy = dependencies.get("native_policy")
        if policy is not None and policy not in NATIVE_POLICIES:
            issues.append("[dependencies].native_policy: %r is not one of %s" % (policy, ", ".join(NATIVE_POLICIES)))
        for field in ("forbidden_namespaces", "build"):
            value = dependencies.get(field)
            if value is not None:
                # Optional lists may legitimately be empty (e.g. a package
                # with no forbidden namespaces yet).
                _validate_str_list(issues, value, "[dependencies].%s" % field, require_nonempty=False)
        extras = dependencies.get("extras")
        if isinstance(extras, dict):
            for group, requirements in extras.items():
                _validate_str_list(issues, requirements, "[dependencies].extras.%s" % group)
        edges = dependencies.get("allowed_edges", [])
        if isinstance(edges, list):
            for i, edge in enumerate(edges):
                where = "[[dependencies.allowed_edges]] #%d" % i
                if not isinstance(edge, dict):
                    issues.append("%s: expected a table" % where)
                    continue
                unknown = set(edge) - {"from_prefix", "to_prefixes", "external"}
                for key in sorted(unknown):
                    issues.append("%s: unknown field %r" % (where, key))
                for field in ("from_prefix", "to_prefixes", "external"):
                    if field in edge and not isinstance(edge[field], list):
                        if field == "from_prefix":
                            if not isinstance(edge[field], str):
                                issues.append("%s.from_prefix: expected a string" % where)
                if "from_prefix" not in edge:
                    issues.append("%s: missing required field 'from_prefix'" % where)
                elif isinstance(edge["from_prefix"], str) and not _PREFIX_RE.match(edge["from_prefix"]):
                    issues.append("%s.from_prefix: invalid module prefix %r" % (where, edge["from_prefix"]))
                to_prefixes = edge.get("to_prefixes")
                if (
                    not isinstance(to_prefixes, list)
                    or not to_prefixes
                    or not all(isinstance(p, str) and _PREFIX_RE.match(p) for p in to_prefixes)
                ):
                    issues.append("%s.to_prefixes: expected a non-empty list of module prefixes" % where)
                external = edge.get("external", [])
                if not isinstance(external, list) or not all(isinstance(p, str) for p in external):
                    issues.append("%s.external: expected a list of module names" % where)

    _check_table(
        issues,
        data,
        "compatibility",
        {
            "python": "list",
            "platforms": "list",
            "dependency_ranges": "table",
            "deprecation_policy": "string",
        },
    )
    compatibility = data.get("compatibility", {})
    if isinstance(compatibility, dict):
        for field in ("python", "platforms"):
            _validate_str_list(issues, compatibility.get(field), "[compatibility].%s" % field)
        policy = compatibility.get("deprecation_policy")
        if isinstance(policy, str) and not policy.strip():
            issues.append("[compatibility].deprecation_policy: must not be empty")
        ranges = compatibility.get("dependency_ranges")
        if isinstance(ranges, dict):
            for name, value in ranges.items():
                if not isinstance(value, str):
                    issues.append("[compatibility].dependency_ranges.%s: expected a string range" % name)

    _check_table(
        issues,
        data,
        "contracts",
        {"supported_exports": "table", "behavioral_fixtures": "?list", "activation_api": "?table"},
    )
    contracts = data.get("contracts", {})
    if isinstance(contracts, dict):
        exports = contracts.get("supported_exports")
        if isinstance(exports, dict):
            if not exports:
                issues.append("[contracts].supported_exports: at least one module is required")
            for module, names in exports.items():
                if not _PREFIX_RE.match(module):
                    issues.append("[contracts].supported_exports: invalid module name %r" % module)
                _validate_str_list(issues, names, "[contracts].supported_exports[%r]" % module)
        fixtures = contracts.get("behavioral_fixtures", [])
        if isinstance(fixtures, list):
            for i, fixture in enumerate(fixtures):
                if not isinstance(fixture, list) or not all(isinstance(c, str) for c in fixture):
                    issues.append("[contracts].behavioral_fixtures[%d]: expected an array of command parts" % i)
        api = contracts.get("activation_api")
        if isinstance(api, dict):
            unknown = set(api) - {"module", "activate", "deactivate"}
            for key in sorted(unknown):
                issues.append("[contracts].activation_api: unknown field %r" % key)
            for field in ("module", "activate"):
                if field not in api:
                    issues.append("[contracts].activation_api: missing required field %r" % field)

    _check_table(
        issues,
        data,
        "validation",
        {"consumer_contract_suites": "?list", "supported_version_combinations": "?list"},
    )

    _check_table(
        issues,
        data,
        "release",
        {
            "build_command": "?list",
            "artifact_expectations": "?list",
            "entry_points": "?table",
            "resources": "?list",
            "publishing_owner": "string",
            "rollback_procedure": "string",
        },
    )
    release = data.get("release", {})
    if isinstance(release, dict):
        for field in ("publishing_owner", "rollback_procedure"):
            value = release.get(field)
            if isinstance(value, str) and not value.strip():
                issues.append("[release].%s: must not be empty" % field)
        for field in ("artifact_expectations", "resources"):
            value = release.get(field)
            if value is not None:
                _validate_str_list(issues, value, "[release].%s" % field)
        entry_points = release.get("entry_points")
        if isinstance(entry_points, dict):
            for group, entries in entry_points.items():
                _validate_str_list(issues, entries, "[release].entry_points[%r]" % group)

    return issues


def load_manifest(path: Path) -> Manifest:
    """Parse and validate package-contract.toml; raise ManifestError on failure."""
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ManifestError(path, ["manifest cannot be read: %s" % exc]) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(path, ["manifest is not valid TOML: %s" % exc]) from exc
    issues = validate_manifest(data)
    if issues:
        raise ManifestError(path, issues)
    return Manifest(path, data)
