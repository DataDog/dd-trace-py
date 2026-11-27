"""Provider evaluation of large JSON flag values from the shared ``scale-json`` suite.

The cases come from the ``scale-json`` evaluation suite of DataDog/ffe-system-test-data,
pinned to ``FIXTURE_COMMIT``. The suite is about 16 MB, so it is fetched on first use
instead of being checked in, and each file is verified against a SHA-256 checksum. Set
``FFE_SCALE_JSON_DIR`` to the ``evaluation-suites/scale-json`` directory of a checkout at
that commit to skip the download.

Each configuration evaluates one case through ``DataDogProvider.resolve_object_details`` and
checks its value and reason before it measures:

* ``repeat``: the same flag again, under the whole suite's configuration loaded once.
* ``first``: the first evaluation after a configuration load. Each loop loads a configuration
  that holds only the case's flag, outside the timed region, then times one evaluation.

Set ``FFE_SCALE_JSON_TRACEMALLOC=1`` to print, on stderr, the Python memory one evaluation
allocates at its peak and the memory it leaves allocated, measured with ``tracemalloc``.
"""

import hashlib
import json
import os
from pathlib import Path
import sys
import time
import tracemalloc
import urllib.request

import bm


FIXTURE_COMMIT = "52205a7155f9c28c5bdd29b5897478e789035bfd"
FIXTURE_FILES = {
    "ufc-config.json": "b1d6ec71bea1bdf8cb66ccc8fd30d7d06d3f4bfa1b5c9eab372b693e5174bee0",
    "cases/json-shapes.json": "a695079b8a43d52d0d762682eca7f4306f4722dcd5eabd121d3af86f368ddb19",
    "cases/json-sizes.json": "3ccc9968726b22be3a76c97875753d786ba2535811a7cfe88e261927c3829bb6",
    "cases/json-values.json": "6bdedd35e087a9fda3bb8bc3c4d5426e2c4ee731564dd161176905263f58f01d",
}
FIXTURE_URL = (
    "https://raw.githubusercontent.com/DataDog/ffe-system-test-data/"
    + FIXTURE_COMMIT
    + "/evaluation-suites/scale-json/"
)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(name, path, checksum):
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.{os.getpid()}.partial")
    last_error = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(FIXTURE_URL + name, timeout=60) as response, open(partial, "wb") as f:
                while chunk := response.read(1 << 20):
                    f.write(chunk)
            break
        except OSError as e:
            last_error = e
            time.sleep(2**attempt)
    else:
        raise RuntimeError(f"failed to download {FIXTURE_URL + name}") from last_error
    if _sha256(partial) != checksum:
        partial.unlink()
        raise RuntimeError(f"checksum mismatch for {FIXTURE_URL + name}")
    partial.replace(path)


def _fixture_dir():
    override = os.environ.get("FFE_SCALE_JSON_DIR")
    if override:
        root = Path(override)
        for name, checksum in FIXTURE_FILES.items():
            if _sha256(root / name) != checksum:
                raise RuntimeError(f"{root / name} does not match ffe-system-test-data@{FIXTURE_COMMIT}")
        return root

    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    root = cache / "ddtrace-benchmarks" / "ffe-scale-json" / FIXTURE_COMMIT
    for name, checksum in FIXTURE_FILES.items():
        path = root / name
        if not (path.is_file() and _sha256(path) == checksum):
            _download(name, path, checksum)
    return root


class _DiscardingExposureWriter:
    def enqueue(self, event):
        pass


class OpenFeatureScaleJson(bm.Scenario):
    # Case file in the suite, without ".json".
    case_file: str
    # Index of the case in that file.
    case_index: int
    # "repeat" or "first".
    mode: str

    cprofile_loops: int = 0

    def _setup(self):
        from openfeature.evaluation_context import EvaluationContext

        from ddtrace.internal.openfeature import _provider
        from ddtrace.internal.openfeature._native import process_ffe_configuration

        root = _fixture_dir()
        with open(root / "cases" / f"{self.case_file}.json") as f:
            case = json.load(f)[self.case_index]
        with open(root / "ufc-config.json") as f:
            config = json.load(f)
        flag = case["flag"]
        if self.mode == "first":
            config["flags"] = {flag: config["flags"][flag]}
        elif self.mode != "repeat":
            raise ValueError(f"unknown mode: {self.mode}")

        # Exposures go to a discarding writer, so the loop measures evaluation work only.
        _provider.get_exposure_writer = lambda: _DiscardingExposureWriter()
        provider = _provider.DataDogProvider()
        assert process_ffe_configuration(config)
        ctx = EvaluationContext(targeting_key=case["targetingKey"], attributes=case["attributes"])
        default = case["defaultValue"]

        def evaluate():
            return provider.resolve_object_details(flag, default, ctx)

        def reload():
            assert process_ffe_configuration(config)

        details = evaluate()
        expected = case["result"]
        assert details.value == expected["value"], f"unexpected value for {flag}"
        reason = getattr(details.reason, "value", details.reason)
        assert reason == expected["reason"], (flag, reason, expected["reason"])
        return evaluate, reload

    def _report_tracemalloc(self, evaluate, reload):
        peaks = []
        retained = []
        tracemalloc.start()
        try:
            for _ in range(5):
                if self.mode == "first":
                    reload()
                before = tracemalloc.get_traced_memory()[0]
                tracemalloc.reset_peak()
                details = evaluate()
                current, peak = tracemalloc.get_traced_memory()
                peaks.append(peak - before)
                retained.append(current - before)
                del details
        finally:
            tracemalloc.stop()
        print(
            f"TRACEMALLOC {self.scenario_name} peak_bytes={min(peaks)} retained_bytes={min(retained)}",
            file=sys.stderr,
        )

    def run(self):
        evaluate, self._reload = self._setup()
        if os.environ.get("FFE_SCALE_JSON_TRACEMALLOC") == "1":
            self._report_tracemalloc(evaluate, self._reload)

        def _(loops):
            for _ in range(loops):
                evaluate()

        yield _

    def _pyperf(self, loops):
        if self.mode == "repeat":
            return self._timed_run(loops)

        # Time only the first evaluation after each configuration load.
        run = next(self.run())
        total = 0.0
        for _ in range(loops):
            self._reload()
            t0 = time.perf_counter()
            run(1)
            total += time.perf_counter() - t0
        return total
