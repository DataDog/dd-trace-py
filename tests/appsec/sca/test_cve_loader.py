"""Tests for the static CVE data loader."""

from ddtrace.appsec.sca._cve_loader import _any_version_matches
from ddtrace.appsec.sca._cve_loader import _compound_constraint_matches
from ddtrace.appsec.sca._cve_loader import _version_matches
from ddtrace.appsec.sca._cve_loader import load_cve_targets


class TestVersionMatches:
    def test_less_than_match(self):
        assert _version_matches("2.31.0", "<2.32.0") is True

    def test_less_than_no_match(self):
        assert _version_matches("2.32.0", "<2.32.0") is False

    def test_less_than_equal_match(self):
        assert _version_matches("2.32.0", "<=2.32.0") is True

    def test_less_than_equal_no_match(self):
        assert _version_matches("2.32.1", "<=2.32.0") is False

    def test_greater_than_match(self):
        assert _version_matches("3.0.0", ">2.32.0") is True

    def test_greater_than_no_match(self):
        assert _version_matches("2.32.0", ">2.32.0") is False

    def test_equal_match(self):
        assert _version_matches("2.32.0", "==2.32.0") is True

    def test_equal_no_match(self):
        assert _version_matches("2.32.1", "==2.32.0") is False

    def test_single_equal_match(self):
        """Single '=' treated as '=='."""
        assert _version_matches("9.5.0", "= 9.5.0") is True

    def test_single_equal_no_match(self):
        assert _version_matches("9.5.1", "= 9.5.0") is False

    def test_invalid_installed_version(self):
        assert _version_matches("not-a-version", "<2.32.0") is False

    def test_invalid_constraint(self):
        assert _version_matches("2.32.0", "bad") is False


class TestCompoundConstraintMatches:
    def test_range_within(self):
        """>=42.2.0, <42.2.28 with 42.2.15 -> True."""
        assert _compound_constraint_matches("42.2.15", ">=42.2.0, <42.2.28") is True

    def test_range_below(self):
        """>=42.2.0, <42.2.28 with 42.1.0 -> False."""
        assert _compound_constraint_matches("42.1.0", ">=42.2.0, <42.2.28") is False

    def test_range_above(self):
        """>=42.2.0, <42.2.28 with 42.2.28 -> False (exclusive upper)."""
        assert _compound_constraint_matches("42.2.28", ">=42.2.0, <42.2.28") is False

    def test_range_boundary_inclusive(self):
        """>=42.2.0, <42.2.28 with 42.2.0 -> True (inclusive lower)."""
        assert _compound_constraint_matches("42.2.0", ">=42.2.0, <42.2.28") is True

    def test_single_constraint(self):
        """A single constraint without comma still works."""
        assert _compound_constraint_matches("2.31.0", "<2.32.0") is True

    def test_empty_string(self):
        assert _compound_constraint_matches("2.31.0", "") is False


class TestAnyVersionMatches:
    def test_or_logic_first_matches(self):
        assert _any_version_matches("8.1.0", ["<8.2.0", ">9.5.0"]) is True

    def test_or_logic_second_matches(self):
        assert _any_version_matches("10.0.0", ["<8.2.0", ">9.5.0"]) is True

    def test_or_logic_none_match(self):
        assert _any_version_matches("9.0.0", ["<8.2.0", ">9.5.0"]) is False

    def test_exact_match_in_list(self):
        assert _any_version_matches("9.5.0", ["= 9.5.0"]) is True

    def test_empty_list(self):
        assert _any_version_matches("1.0.0", []) is False

    def test_compound_in_list(self):
        """OR list with a compound AND constraint."""
        assert _any_version_matches("42.2.15", ["<1.0.0", ">=42.2.0, <42.2.28"]) is True

    def test_compound_in_list_no_match(self):
        assert _any_version_matches("42.3.0", ["<1.0.0", ">=42.2.0, <42.2.28"]) is False


class TestLoadCveTargets:
    def test_loads_applicable_targets(self):
        """CVE-2024-35195 applies to requests < 2.32.0."""
        installed = {"requests": "2.28.0"}
        targets = load_cve_targets(installed)
        cve_ids = [t["cve_id"] for t in targets]
        assert "CVE-2024-35195" in cve_ids

    def test_skips_non_matching_version(self):
        """requests 2.32.5 is not vulnerable to CVE-2024-35195."""
        installed = {"requests": "2.32.5"}
        targets = load_cve_targets(installed)
        cve_ids = [t["cve_id"] for t in targets]
        assert "CVE-2024-35195" not in cve_ids

    def test_skips_uninstalled_packages(self):
        """Packages not in installed dict are skipped."""
        installed = {}
        targets = load_cve_targets(installed)
        assert targets == []

    def test_multiple_cves_for_same_package(self):
        """jinja2 < 3.1.3 is vulnerable to both CVE-2024-22195 and CVE-2024-56201."""
        installed = {"jinja2": "3.1.2"}
        targets = load_cve_targets(installed)
        cve_ids = {t["cve_id"] for t in targets}
        assert "CVE-2024-22195" in cve_ids
        assert "CVE-2024-56201" in cve_ids

    def test_partial_version_match(self):
        """jinja2 3.1.4 matches <3.1.5 but not <3.1.3."""
        installed = {"jinja2": "3.1.4"}
        targets = load_cve_targets(installed)
        cve_ids = {t["cve_id"] for t in targets}
        assert "CVE-2024-22195" not in cve_ids  # fixed in 3.1.3
        assert "CVE-2024-56201" in cve_ids  # fixed in 3.1.5

    def test_target_has_expected_fields(self):
        installed = {"requests": "2.28.0"}
        targets = load_cve_targets(installed)
        assert len(targets) >= 1
        t = targets[0]
        assert "target" in t
        assert "dependency_name" in t
        assert "cve_id" in t
        assert t["dependency_name"] == "requests"
        assert ":" in t["target"]  # qualified name format

    def test_multiple_packages(self):
        """Multiple vulnerable packages are all detected."""
        installed = {
            "requests": "2.28.0",
            "urllib3": "1.26.10",
            "cryptography": "41.0.0",
        }
        targets = load_cve_targets(installed)
        dep_names = {t["dependency_name"] for t in targets}
        assert "requests" in dep_names
        assert "urllib3" in dep_names
        assert "cryptography" in dep_names

    def test_no_duplicate_entries(self):
        """No duplicate entries for the same CVE+target pair."""
        installed = {"requests": "2.28.0"}
        targets = load_cve_targets(installed)
        pairs = [(t["cve_id"], t["target"]) for t in targets]
        assert len(pairs) == len(set(pairs))

    def test_multiple_targets_per_entry(self, tmp_path):
        """An entry with multiple targets emits one result per target."""
        import json
        from unittest.mock import patch

        cve_json = {
            "targets": [
                {
                    "id": "vuln-test",
                    "targets": ["mod.a:func_a", "mod.b:func_b"],
                    "lang": "python",
                    "dependency_name": "testpkg",
                    "package_versions": ["<2.0.0"],
                    "vulnerability": {
                        "id": "CVE-TEST-MULTI",
                        "severity": "HIGH",
                        "description": "test",
                    },
                }
            ]
        }
        json_path = tmp_path / "test_cve_data.json"
        json_path.write_text(json.dumps(cve_json))

        with patch("ddtrace.appsec.sca._cve_loader._CVE_DATA_PATH", str(json_path)):
            targets = load_cve_targets({"testpkg": "1.0.0"})

        assert len(targets) == 2
        target_names = {t["target"] for t in targets}
        assert target_names == {"mod.a:func_a", "mod.b:func_b"}
        assert all(t["cve_id"] == "CVE-TEST-MULTI" for t in targets)

    def test_multiple_version_constraints(self, tmp_path):
        """An entry with multiple version constraints uses OR logic."""
        import json
        from unittest.mock import patch

        cve_json = {
            "targets": [
                {
                    "id": "vuln-test",
                    "targets": ["mod:func"],
                    "lang": "python",
                    "dependency_name": "testpkg",
                    "package_versions": ["<1.0.0", ">=2.0.0, <2.5.0"],
                    "vulnerability": {
                        "id": "CVE-TEST-VERSIONS",
                        "severity": "HIGH",
                        "description": "test",
                    },
                }
            ]
        }
        json_path = tmp_path / "test_cve_data.json"
        json_path.write_text(json.dumps(cve_json))

        with patch("ddtrace.appsec.sca._cve_loader._CVE_DATA_PATH", str(json_path)):
            # Version 0.9 matches first constraint (<1.0.0)
            assert len(load_cve_targets({"testpkg": "0.9.0"})) == 1
            # Version 2.3.0 matches second constraint (>=2.0.0, <2.5.0)
            assert len(load_cve_targets({"testpkg": "2.3.0"})) == 1
            # Version 1.5.0 matches neither
            assert len(load_cve_targets({"testpkg": "1.5.0"})) == 0
            # Version 2.5.0 matches neither (upper bound exclusive)
            assert len(load_cve_targets({"testpkg": "2.5.0"})) == 0
