"""Tests for the static CVE data loader."""

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

    def test_invalid_installed_version(self):
        assert _version_matches("not-a-version", "<2.32.0") is False

    def test_invalid_constraint(self):
        assert _version_matches("2.32.0", "bad") is False


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
