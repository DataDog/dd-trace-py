"""
Bazel offline mode support for the pytest test optimization plugin.

Two independent modes are controlled by environment variables:

- Manifest mode (DD_TEST_OPTIMIZATION_MANIFEST_FILE):
  Read settings, known tests, test management, and skippable tests from local
  cached files inside the .testoptimization directory instead of making HTTP
  requests to the Datadog backend. Critical for Bazel's hermetic sandbox.

- Payload-files mode (DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES):
  Write test event and coverage payloads as JSON files to
  TEST_UNDECLARED_OUTPUTS_DIR instead of sending them over HTTP.
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import typing as t

from ddtrace.internal.settings import env
from ddtrace.testing.internal.constants import DD_TEST_OPTIMIZATION_MANIFEST_FILE
from ddtrace.testing.internal.constants import DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES
from ddtrace.testing.internal.constants import MANIFEST_VERSION_DDTEST
from ddtrace.testing.internal.constants import SUPPORTED_MANIFEST_VERSIONS
from ddtrace.testing.internal.constants import TEST_UNDECLARED_OUTPUTS_DIR
from ddtrace.testing.internal.utils import asbool


log = logging.getLogger(__name__)


def resolve_rlocation(path: str) -> str:
    """
    Resolve a Bazel runfile rlocation path to an absolute filesystem path.

    Bazel exposes test data files via a runfiles tree. The partial path provided
    in an rlocation must be resolved to an absolute path using the runfiles
    manifest or directory. This mirrors the Go implementation in CHANGES.md.

    Resolution order:
    1. If the path already exists on disk, return it as-is.
    2. Try joining with RUNFILES_DIR.
    3. Scan RUNFILES_MANIFEST_FILE for a matching entry.
    4. Try joining with TEST_SRCDIR.
    5. Fall back to returning the original path unchanged.
    """
    if os.path.exists(path):
        return path

    if runfiles_dir := env.get("RUNFILES_DIR"):
        candidate = os.path.join(runfiles_dir, path)
        if os.path.exists(candidate):
            return candidate

    if manifest_file := env.get("RUNFILES_MANIFEST_FILE"):
        try:
            with open(manifest_file) as f:
                for line in f:
                    line = line.rstrip("\n")
                    sep = line.find(" ")
                    if sep > 0 and line[:sep] == path:
                        return line[sep + 1 :]
        except OSError:
            pass

    if test_srcdir := env.get("TEST_SRCDIR"):
        candidate = os.path.join(test_srcdir, path)
        if os.path.exists(candidate):
            return candidate

    return path


def _parse_manifest_version(raw_line: str) -> str:
    """Extract the version value from a raw manifest line.

    Accepts both plain ``"1"`` and assignment syntax ``"version=1"`` /
    ``"version = 1"``, matching the Go ``parseManifestVersion`` helper.
    """
    line = raw_line.strip()
    if "=" in line:
        name, _, value = line.partition("=")
        if name.strip() == "version":
            return value.strip()
    return line


def _read_manifest_version(manifest_path: str) -> t.Optional[int]:
    """
    Read manifest.txt and return the version integer, or None on failure.

    Manifest mode is disabled when None is returned — no HTTP fallback is
    attempted (Bazel hermeticity requires a hard boundary).

    The parser skips blank lines and supports both plain version numbers
    (``"1"``) and assignment syntax (``"version=1"``), matching the Go
    implementation.
    """
    try:
        with open(manifest_path) as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                version_str = _parse_manifest_version(stripped)
                try:
                    version = int(version_str)
                except ValueError:
                    log.warning(
                        "Could not parse manifest version %r in %s — disabling manifest mode", stripped, manifest_path
                    )
                    return None

                if version not in SUPPORTED_MANIFEST_VERSIONS:
                    log.warning(
                        "Unsupported .testoptimization manifest version %d — disabling manifest mode",
                        version,
                    )
                    return None
                return version

        # File was empty or only blank lines
        log.warning("Empty manifest file %s — disabling manifest mode", manifest_path)
        return None
    except OSError as e:
        log.warning("Could not read manifest file %s: %s — disabling manifest mode", manifest_path, e)
        return None


class OfflineMode:
    """
    Resolved state of whether Bazel offline modes are active.

    Use ``get_offline_mode()`` to obtain the module-level singleton.
    """

    def __init__(self) -> None:
        self.manifest_enabled: bool = False
        self.apply_cached_skipping: bool = False
        self.payload_files_enabled: bool = False
        self.test_optimization_dir: t.Optional[str] = None
        self.output_dir: t.Optional[str] = None

        # --- manifest mode (input side) ---
        manifest_env = env.get(DD_TEST_OPTIMIZATION_MANIFEST_FILE)
        if manifest_env:
            resolved = resolve_rlocation(manifest_env)
            version = _read_manifest_version(resolved)
            if version is not None:
                self.manifest_enabled = True
                self.apply_cached_skipping = version >= MANIFEST_VERSION_DDTEST
                self.test_optimization_dir = os.path.dirname(resolved)
                log.debug(
                    "Manifest mode enabled: .testoptimization dir = %s, apply_cached_skipping = %s",
                    self.test_optimization_dir,
                    self.apply_cached_skipping,
                )

        # --- payload-files mode (output side) ---
        if asbool(env.get(DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES)):
            output_dir = env.get(TEST_UNDECLARED_OUTPUTS_DIR)
            if output_dir:
                self.payload_files_enabled = True
                self.output_dir = output_dir
                log.debug("Payload-files mode enabled: output dir = %s", self.output_dir)
            else:
                log.warning(
                    "%s is true but %s is not set — payload-files mode disabled",
                    DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES,
                    TEST_UNDECLARED_OUTPUTS_DIR,
                )

    def cache_file_path(self, relative: str) -> t.Optional[str]:
        """
        Return the absolute path to a cache file inside .testoptimization.

        Returns None when manifest mode is inactive. The ``relative`` path
        should use forward slashes, e.g. ``"cache/http/settings.json"``.
        """
        if not self.manifest_enabled or not self.test_optimization_dir:
            return None
        return os.path.join(self.test_optimization_dir, *relative.split("/"))

    def payload_output_dir(self, category: str) -> t.Optional[str]:
        """
        Return the directory for payload output files under TEST_UNDECLARED_OUTPUTS_DIR.

        ``category`` is ``"tests"``, ``"coverage"``, or ``"telemetry"``.
        Returns None when payload-files mode is inactive.
        """
        if not self.payload_files_enabled or not self.output_dir:
            return None
        return os.path.join(self.output_dir, "payloads", category)


_offline_mode: t.Optional[OfflineMode] = None


def get_offline_mode() -> OfflineMode:
    """Return the cached OfflineMode singleton, initializing it on first call."""
    global _offline_mode
    if _offline_mode is None:
        _offline_mode = OfflineMode()
    return _offline_mode


# ---------------------------------------------------------------------------
# Payload file writing
# ---------------------------------------------------------------------------

# Thread-safe counter for unique payload file names across writer instances.
_payload_file_counter = itertools.count()


def write_payload_file(output_dir: str, payload: t.Any, kind: str) -> None:
    """
    Write a payload dict as a JSON file under ``output_dir``.

    For tests and coverage, files are named ``{kind}-{ts}-{pid}-{seq}.json``.
    For telemetry, filenames are ordinal-first (``telemetry-{seq_padded}-{pid}.json``)
    so they sort lexicographically in emission order for deterministic replay.
    Both patterns match the Go implementation.

    The write is atomic: we write to a temp file and rename, so readers never
    see a partial file.
    """
    try:
        os.makedirs(output_dir, exist_ok=True)
        seq = next(_payload_file_counter)
        import time

        if kind == "telemetry":
            # Telemetry replay order matters — keep filenames lexicographically ordered by sequence.
            name = f"{kind}-{seq:020d}-{os.getpid()}.json"
        else:
            name = f"{kind}-{time.time_ns()}-{os.getpid()}-{seq}.json"
        dest = os.path.join(output_dir, name)
        tmp = dest + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, dest)
        log.debug("Wrote payload file: %s", dest)
    except Exception as e:
        log.warning("Error writing payload file to %s: %s", output_dir, e)
