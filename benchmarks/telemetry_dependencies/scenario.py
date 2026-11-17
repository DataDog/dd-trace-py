"""Benchmark: telemetry dependency reporting (collect_report) with SCA scenarios.

Measures the cost of dependency reporting in the telemetry heartbeat path
under different scenarios: first heartbeat, idle, CVE registration, SCA hits.

Module discovery and distribution lookup are replaced by plain stubs installed
outside the timed region, so only collect_report() itself is measured.

Falls back to the older update_imported_dependencies API when the installed
ddtrace (e.g. a baseline wheel) has no DependencyTracker.
"""

import contextlib
import sys
import time

from bm import Scenario

from ddtrace.internal.settings._telemetry import config as telemetry_config


try:
    from ddtrace.internal.settings.appsec_telemetry import config as appsec_telemetry_config
except ModuleNotFoundError as error:
    if error.name != "ddtrace.internal.settings.appsec_telemetry":
        raise

    from ddtrace.internal.settings._config import config as tracer_config

    def _enable_sca():
        tracer_config._sca_enabled = True

else:

    def _enable_sca():
        appsec_telemetry_config.SCA_ENABLED = True


try:
    from ddtrace.internal.telemetry import dependency_tracker as _dependency_tracker
    from ddtrace.internal.telemetry.dependency import DependencyEntry
    from ddtrace.internal.telemetry.dependency import attach_reachability_metadata
    from ddtrace.internal.telemetry.dependency import register_cve_metadata
    from ddtrace.internal.telemetry.dependency_tracker import DependencyTracker

    HAS_DEPENDENCY_TRACKER = True
except ImportError:
    HAS_DEPENDENCY_TRACKER = False

try:
    from ddtrace.internal.telemetry.data import update_imported_dependencies as _old_update

    HAS_OLD_API = True
except ImportError:
    HAS_OLD_API = False

if not HAS_OLD_API:
    try:
        from ddtrace.internal.telemetry.dependency_tracker import update_imported_dependencies as _old_update

        HAS_OLD_API = True
    except ImportError:
        pass


def _populate_tracker(tracker, n, sca_enabled):
    """Pre-populate a DependencyTracker with n dependencies as already-reported."""
    if sca_enabled:
        _enable_sca()
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


class _StubModules:
    def __init__(self, module_names=()):
        self._module_names = module_names

    def get_newly_imported_modules(self, already_imported):
        return self._module_names


@contextlib.contextmanager
def _stubbed(target, **attrs):
    """Temporarily replace attributes on target; cheaper and cycle-free compared to mock.patch."""
    saved = {name: getattr(target, name) for name in attrs}
    for name, value in attrs.items():
        setattr(target, name, value)
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(target, name, value)


@contextlib.contextmanager
def _collect_report_stubs(module_names=(), dists=None):
    dists = dists or {}
    with (
        _stubbed(telemetry_config, DEPENDENCY_COLLECTION=True),
        _stubbed(
            _dependency_tracker,
            modules=_StubModules(module_names),
            get_module_distribution_versions=dists.get,
        ),
    ):
        yield


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
        module_names = {"mod-%d" % i for i in range(n)}
        dists = {"mod-%d" % i: ("package-%d" % i, "%d.0.0" % i) for i in range(n)}
        if self.sca_enabled:
            _enable_sca()

        total = 0.0
        with _collect_report_stubs(module_names, dists):
            for _ in range(loops):
                tracker = DependencyTracker()
                t0 = time.perf_counter()
                tracker.collect_report()
                total += time.perf_counter() - t0
        return total

    def _run_idle(self, loops):
        """All deps already reported, nothing new."""
        tracker = DependencyTracker()
        _populate_tracker(tracker, self.num_deps, self.sca_enabled)

        total = 0.0
        with _collect_report_stubs():
            for _ in range(loops):
                t0 = time.perf_counter()
                tracker.collect_report()
                total += time.perf_counter() - t0
        return total

    def _run_cve_registration(self, loops):
        """Deps have CVEs registered with reached=[]."""
        total = 0.0
        with _collect_report_stubs():
            for _ in range(loops):
                tracker = DependencyTracker()
                _populate_tracker(tracker, self.num_deps, self.sca_enabled)
                _register_cves(tracker, self.num_deps, self.pct_cve, self.cves_per_dep)
                t0 = time.perf_counter()
                tracker.collect_report()
                total += time.perf_counter() - t0
        return total

    def _run_sca_hits(self, loops):
        """Deps have reachability hits attached."""
        total = 0.0
        with _collect_report_stubs():
            for _ in range(loops):
                tracker = DependencyTracker()
                _populate_tracker(tracker, self.num_deps, self.sca_enabled)
                _register_cves(tracker, self.num_deps, self.pct_cve, self.cves_per_dep)
                # Mark CVE registrations as sent (simulates previous heartbeat)
                for entry in tracker._imported_dependencies.values():
                    entry.mark_all_metadata_sent()
                _attach_hits(tracker, self.num_deps, self.pct_cve, self.cves_per_dep)
                t0 = time.perf_counter()
                tracker.collect_report()
                total += time.perf_counter() - t0
        return total

    def _run_old_api(self, loops):
        """Fallback: benchmark the old update_imported_dependencies().

        Only supports first/idle phases since CVE/SCA features don't exist.
        """
        n = self.num_deps
        old_module = sys.modules[_old_update.__module__]

        if self.phase == "first":
            module_names = ["mod-%d" % i for i in range(n)]
            dists = {"mod-%d" % i: ("package-%d" % i, "%d.0.0" % i) for i in range(n)}

            total = 0.0
            with _stubbed(old_module, get_module_distribution_versions=dists.get):
                for _ in range(loops):
                    already_imported = {}
                    t0 = time.perf_counter()
                    _old_update(already_imported, module_names)
                    total += time.perf_counter() - t0
            return total
        else:
            # idle / cve_registration / sca_hits all reduce to "nothing to do"
            # on the old API since there's no re-report logic. There,
            # `already_imported` maps name -> version string.
            already_imported = {"package-%d" % i: "%d.0.0" % i for i in range(n)}

            total = 0.0
            with _stubbed(old_module, get_module_distribution_versions=lambda module_name: None):
                for _ in range(loops):
                    t0 = time.perf_counter()
                    _old_update(already_imported, [])
                    total += time.perf_counter() - t0
            return total
