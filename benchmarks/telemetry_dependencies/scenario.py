"""Benchmark: telemetry dependency reporting (collect_report) with SCA scenarios.

Measures the cost of dependency reporting in the telemetry heartbeat path
under different scenarios: first heartbeat, idle, CVE registration, SCA hits.

Backward-compatible with both the new DependencyTracker API (this branch)
and the old update_imported_dependencies API (main).
"""

import time
from unittest.mock import patch

from bm import Scenario


# --- Backward-compatible imports ---
# New API (this branch): DependencyTracker with collect_report()
try:
    from ddtrace.internal.telemetry.dependency import DependencyEntry
    from ddtrace.internal.telemetry.dependency import attach_reachability_metadata
    from ddtrace.internal.telemetry.dependency import register_cve_metadata
    from ddtrace.internal.telemetry.dependency_tracker import DependencyTracker

    HAS_DEPENDENCY_TRACKER = True
except ImportError:
    HAS_DEPENDENCY_TRACKER = False

# Old API (main): update_imported_dependencies from data.py
try:
    from ddtrace.internal.telemetry.data import update_imported_dependencies as _old_update

    HAS_OLD_API = True
except ImportError:
    HAS_OLD_API = False

# Fallback: standalone update_imported_dependencies from dependency_tracker.py
if not HAS_OLD_API:
    try:
        from ddtrace.internal.telemetry.dependency_tracker import update_imported_dependencies as _old_update

        HAS_OLD_API = True
    except ImportError:
        pass


def _populate_tracker(tracker, n, sca_enabled):
    """Pre-populate a DependencyTracker with n dependencies as already-reported."""
    if sca_enabled:
        from ddtrace.internal.settings._config import config as tracer_config

        tracer_config._sca_enabled = True
    for i in range(n):
        name = "package-%d" % i
        meta = [] if sca_enabled else None
        tracker._imported_dependencies[name] = DependencyEntry(name=name, version="%d.0.0" % i, metadata=meta)
        tracker._imported_dependencies[name].mark_initial_sent()
        tracker._imported_dependencies[name].mark_all_metadata_sent()


def _register_cves(tracker, n_deps, pct, cves_per_dep):
    """Register CVEs (reached=[]) on pct% of deps."""
    count = max(1, n_deps * pct // 100)
    for i in range(count):
        name = "package-%d" % i
        for c in range(cves_per_dep):
            register_cve_metadata(tracker._imported_dependencies, name, "CVE-%d-%d" % (i, c))


def _attach_hits(tracker, n_deps, pct, cves_per_dep):
    """Simulate SCA hook hits on pct% of deps that already have CVEs."""
    count = max(1, n_deps * pct // 100)
    for i in range(count):
        name = "package-%d" % i
        for c in range(cves_per_dep):
            attach_reachability_metadata(
                tracker._imported_dependencies,
                name,
                "CVE-%d-%d" % (i, c),
                "myapp.views%d" % i,
                "handle_%d" % c,
                10 + c,
            )


def _collect_report_no_new_modules(tracker):
    """Run collect_report with no new module discovery (mocked)."""
    with (
        patch("ddtrace.internal.telemetry.dependency_tracker.modules") as mock_modules,
        patch("ddtrace.internal.telemetry.dependency_tracker.config") as mock_config,
    ):
        mock_config.DEPENDENCY_COLLECTION = True
        mock_modules.get_newly_imported_modules.return_value = set()
        return tracker.collect_report()


def _collect_report_new_modules(tracker, module_names, dists):
    """Run collect_report simulating new module discovery."""

    def fake_get_dist(module_name):
        return dists.get(module_name)

    with (
        patch("ddtrace.internal.telemetry.dependency_tracker.modules") as mock_modules,
        patch("ddtrace.internal.telemetry.dependency_tracker.config") as mock_config,
        patch(
            "ddtrace.internal.telemetry.dependency_tracker.get_module_distribution_versions",
            side_effect=fake_get_dist,
        ),
    ):
        mock_config.DEPENDENCY_COLLECTION = True
        mock_modules.get_newly_imported_modules.return_value = module_names
        return tracker.collect_report()


class TelemetryDependencies(Scenario):
    """Benchmark telemetry dependency reporting under different SCA scenarios.

    Measures the cost of collect_report() per heartbeat cycle.
    Falls back to update_imported_dependencies() on older ddtrace versions.
    """

    num_deps: int
    phase: str
    sca_enabled: bool
    pct_cve: int
    cves_per_dep: int

    def _pyperf(self, loops):
        if HAS_DEPENDENCY_TRACKER:
            return self._run_new_api(loops)
        return self._run_old_api(loops)

    def _run_new_api(self, loops):
        """Benchmark using DependencyTracker.collect_report() (branch)."""
        phase = self.phase

        if phase == "first":
            return self._run_first_heartbeat(loops)
        elif phase == "idle":
            return self._run_idle(loops)
        elif phase == "cve_registration":
            return self._run_cve_registration(loops)
        elif phase == "sca_hits":
            return self._run_sca_hits(loops)
        else:
            raise ValueError("Unknown phase: %s" % phase)

    def _run_first_heartbeat(self, loops):
        """All N dependencies discovered as new."""
        n = self.num_deps
        sca = self.sca_enabled
        module_names = {"mod-%d" % i for i in range(n)}
        dists = {"mod-%d" % i: ("package-%d" % i, "%d.0.0" % i) for i in range(n)}

        total = 0.0
        for _ in range(loops):
            tracker = DependencyTracker()
            if sca:
                from ddtrace.internal.settings._config import config as tracer_config

                tracer_config._sca_enabled = True
            t0 = time.perf_counter()
            _collect_report_new_modules(tracker, module_names, dists)
            total += time.perf_counter() - t0
        return total

    def _run_idle(self, loops):
        """All deps already reported, nothing new."""
        tracker = DependencyTracker()
        _populate_tracker(tracker, self.num_deps, self.sca_enabled)

        total = 0.0
        for _ in range(loops):
            t0 = time.perf_counter()
            _collect_report_no_new_modules(tracker)
            total += time.perf_counter() - t0
        return total

    def _run_cve_registration(self, loops):
        """Deps have CVEs registered with reached=[]."""
        total = 0.0
        for _ in range(loops):
            tracker = DependencyTracker()
            _populate_tracker(tracker, self.num_deps, self.sca_enabled)
            _register_cves(tracker, self.num_deps, self.pct_cve, self.cves_per_dep)
            t0 = time.perf_counter()
            _collect_report_no_new_modules(tracker)
            total += time.perf_counter() - t0
        return total

    def _run_sca_hits(self, loops):
        """Deps have reachability hits attached."""
        total = 0.0
        for _ in range(loops):
            tracker = DependencyTracker()
            _populate_tracker(tracker, self.num_deps, self.sca_enabled)
            _register_cves(tracker, self.num_deps, self.pct_cve, self.cves_per_dep)
            # Mark CVE registrations as sent (simulates previous heartbeat)
            for entry in tracker._imported_dependencies.values():
                entry.mark_all_metadata_sent()
            _attach_hits(tracker, self.num_deps, self.pct_cve, self.cves_per_dep)
            t0 = time.perf_counter()
            _collect_report_no_new_modules(tracker)
            total += time.perf_counter() - t0
        return total

    def _run_old_api(self, loops):
        """Fallback: benchmark using old update_imported_dependencies() (main).

        Only supports first/idle phases since CVE/SCA features don't exist.
        """
        n = self.num_deps
        phase = self.phase

        if phase == "first":
            module_names = ["mod-%d" % i for i in range(n)]

            def fake_get_dist(module_name):
                idx = module_name.split("-")[1]
                return ("package-%s" % idx, "%s.0.0" % idx)

            total = 0.0
            for _ in range(loops):
                already_imported = {}
                with patch(
                    "ddtrace.internal.telemetry.data.get_module_distribution_versions",
                    side_effect=fake_get_dist,
                ):
                    t0 = time.perf_counter()
                    _old_update(already_imported, module_names)
                    total += time.perf_counter() - t0
            return total
        else:
            # idle / cve_registration / sca_hits all reduce to "nothing to do"
            # on main since there's no re-report logic. On the old API,
            # `already_imported` maps name -> version string.
            already_imported = {"package-%d" % i: "%d.0.0" % i for i in range(n)}

            def fake_get_dist(module_name):
                return None

            total = 0.0
            for _ in range(loops):
                with patch(
                    "ddtrace.internal.telemetry.data.get_module_distribution_versions",
                    side_effect=fake_get_dist,
                ):
                    t0 = time.perf_counter()
                    _old_update(already_imported, [])
                    total += time.perf_counter() - t0
            return total
