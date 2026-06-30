import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import sys
import sysconfig
import tempfile
import typing as t

from ddtrace.internal import gitmetadata
from ddtrace.internal.packages import Distribution
from ddtrace.internal.packages import _is_bazel_runfiles_dir
from ddtrace.internal.packages import _package_for_root_module_mapping
from ddtrace.internal.settings import env


class Library:
    def __init__(
        self,
        kind: str,
        name: str,
        version: str,
        paths: set[str],
    ) -> None:
        self.kind = kind
        self.name = name
        self.version = version
        self.paths = paths

    def to_dict(self) -> dict[str, t.Any]:
        return {"kind": self.kind, "name": self.name, "version": self.version, "paths": list(self.paths)}

    def __repr__(self) -> str:
        return f"Library(kind={self.kind}, name={self.name}, version={self.version}, paths={self.paths})"


class CodeProvenance:
    def __init__(self) -> None:
        self.libraries: list[Library] = []

        python_stdlib = Library(
            kind="standard library",
            name="stdlib",
            version=platform.python_version(),
            paths=set(
                [
                    sysconfig.get_path("stdlib"),
                    # Though we do handle frozen modules in the stdlib, these
                    # two modules appear as _frozen_importlib and _frozen_importlib_external
                    # in sys.stdlib_module_names where they appear as below
                    # from profiles, we hardcode them here.
                    "<frozen importlib._bootstrap>",
                    "<frozen importlib._bootstrap_external>",
                    "<frozen importlib.util>",
                ]
            ),
        )

        # Add frozen modules that are part of the standard library
        # This is mainly to handle locations like <frozen importlib._bootstrap>.
        # sys.stdlib_module_names was added in Python 3.10
        # For older versions, we could iterate over sys.modules.keys(), but that
        # would include all modules, not just stdlib modules.
        if sys.version_info >= (3, 10):
            for name in sys.stdlib_module_names:
                try:
                    spec = importlib.util.find_spec(name)
                    if spec and spec.origin == "frozen":
                        python_stdlib.paths.add(f"<frozen {spec.name}>")
                except Exception:  # nosec
                    continue

        self.libraries.append(python_stdlib)

        # Native frames pushed by the profiler use a synthetic "<native>" filename.
        # Register it as a "library" so those frames are attributed to third-party code.
        self.libraries.append(
            Library(
                kind="library",
                name="native",
                version="",
                paths={"<native>"},
            )
        )

        module_to_distribution: dict[str, Distribution] = _package_for_root_module_mapping() or {}

        libraries: dict[str, Library] = {}

        site_packages = Path(sysconfig.get_path("purelib"))
        # In Bazel py_binary/py_test targets, packages live in isolated
        # per-package site-packages dirs rather than the single purelib, so
        # we need to search sys.path. We gate this on a Bazel-specific env var
        # to avoid affecting non-Bazel environments.
        _in_bazel = bool(env.get("RUNFILES_DIR") or env.get("RUNFILES_MANIFEST_FILE"))
        # Only consider dependency site-packages dirs *inside the Bazel runfiles
        # tree*, not arbitrary sys.path entries. Bazel puts each dependency in an
        # isolated ``<runfiles>/<dist>/site-packages`` dir (the same layout
        # assumed by packages._root_module). Two kinds of unrelated entries must
        # be excluded, both of which can appear ahead of the runfiles dirs and
        # would otherwise win the first-match lookup below:
        #   - workspace/source roots (basename filter handles these), and
        #   - a host/virtualenv site-packages: a dependency present both there
        #     and in runfiles (e.g. requests) would get the host path recorded,
        #     which never matches the runfiles frames in the profile.
        # ``_is_bazel_runfiles_dir`` also recognizes manifest-mode runfiles
        # (``RUNFILES_MANIFEST_FILE`` without ``RUNFILES_DIR``), where the real
        # dependency dirs can live outside any ``*.runfiles`` tree.
        _sys_path_dirs = (
            [p for p in (Path(s) for s in sys.path if s) if p.name == "site-packages" and _is_bazel_runfiles_dir(p)]
            if _in_bazel
            else []
        )

        for module, dist in module_to_distribution.items():
            name = dist.name
            # special case for __pycache__/filename.cpython-3xx.pyc -> filename.py
            if module.startswith("__pycache__/"):
                module = module[len("__pycache__/") :].split(".")[0] + ".py"

            lib = libraries.get(name)
            if lib is None:
                lib = Library(kind="library", name=name, version=dist.version, paths=set())
                libraries[name] = lib

            # We assume that each module is a directory or a python file
            # relative to site-packages/ directory.
            module_path = site_packages / module
            is_py = module.endswith(".py")
            if not _in_bazel:
                # Standard layout: the package lives in the single purelib dir.
                # We keep recording the purelib path for .py modules even if a
                # stat fails, preserving the original behavior.
                if is_py or module_path.is_dir():
                    lib.paths.add(str(module_path))
            else:
                # In Bazel runfiles each package lives in its own isolated
                # site-packages dir rather than the single purelib. Profiles
                # from the Bazel binary contain runfiles paths, so we must
                # prefer the sys.path (runfiles) location even when the same
                # module name also exists in the host purelib (the shadowed
                # dependency case this feature targets) -- otherwise the
                # recorded purelib path never matches the profile frames.
                # Search sys.path first, including single-file modules (e.g.
                # six.py) whose purelib path does not exist.
                found = False
                for base in _sys_path_dirs:
                    candidate = base / module
                    if candidate.is_dir() or (is_py and candidate.is_file()):
                        lib.paths.add(str(candidate))
                        found = True
                        break
                    if not is_py:
                        candidate_py = base / (module + ".py")
                        if candidate_py.is_file():
                            lib.paths.add(str(candidate_py))
                            found = True
                            break
                # Fall back to the host purelib only when runfiles has nothing.
                if not found and (module_path.is_dir() or (is_py and module_path.is_file())):
                    lib.paths.add(str(module_path))

        # If the user installed their code like a library and is running it as
        # the main package (python -m my_package), and they explicitly specified
        # that that's the main package, make sure it shows up as "my code" in
        # the UI. Do this by leaving the kind blank (but not deleting the
        # library so we can still associate the library with its files)
        _, _, main_package = gitmetadata.get_git_tags()
        if info := libraries.get(main_package, None):
            info.kind = ""

        self.libraries.extend(libraries.values())

    def to_dict(self) -> dict[str, t.Any]:
        return {"v1": [lib.to_dict() for lib in self.libraries]}


_CODE_PROVENANCE_CACHE_PREFIX = "ddtrace-code-provenance"
_CODE_PROVENANCE_CACHE_VERSION = "v1"
_code_provenance_file_path: t.Optional[str] = None


def _safe_mtime_ns(path: t.Optional[str]) -> str:
    if not path:
        return ""
    try:
        return str(Path(path).stat().st_mtime_ns)
    except OSError:
        return ""


def _bazel_runfiles_fingerprint() -> str:
    """Fingerprint the Bazel runfiles site-packages dirs.

    A Bazel target rebuilt in place keeps the same ``sys.path`` strings while
    its dependency set / dist-info contents change, so the sys.path hash alone
    would happily serve a stale ``code-provenance.json`` from a previous build.
    Fold in each runfiles ``site-packages`` dir's mtime plus the names of its
    ``.dist-info`` dirs so a rebuild yields a distinct cache file.

    Returns an ASCII hex digest (or "" outside Bazel, contributing nothing).
    """
    if not (env.get("RUNFILES_DIR") or env.get("RUNFILES_MANIFEST_FILE")):
        return ""
    h = hashlib.sha256()
    for p in sys.path:
        if not p or Path(p).name != "site-packages":
            continue
        # os.fsencode keeps undecodable (surrogate-escaped) paths from raising.
        h.update(os.fsencode(p))
        h.update(b"\x00")
        h.update(_safe_mtime_ns(p).encode("ascii"))
        h.update(b"\x00")
        try:
            for name in sorted(c.name for c in Path(p).iterdir() if c.suffix == ".dist-info"):
                h.update(os.fsencode(name))
                h.update(b"\x00")
        except OSError:
            pass
    return h.hexdigest()


def _cache_basename() -> str:
    purelib = sysconfig.get_path("purelib")
    main_package = env.get("DD_MAIN_PACKAGE", "")
    # Include a stable hash of sys.path so that Bazel py_binary targets with
    # different runfiles directories (same interpreter/prefix, different paths)
    # get distinct cache files and don't serve stale provenance to each other.
    # Preserve sys.path order: the provenance builder searches it in order and
    # stops at the first match, so two targets with the same entries in a
    # different order can resolve different files and must not share a cache.
    # Resolve every entry to an absolute cwd-anchored path: '' (the default
    # current-directory entry) and other relative entries can still surface
    # cwd-local distributions, so two processes that differ only by cwd must not
    # share a cache basename. os.path.abspath('') yields the cwd; os.fsencode
    # keeps undecodable (surrogate-escaped) entries from raising UnicodeEncodeError.
    sys_path_hash = hashlib.sha256(b"\x00".join(os.fsencode(os.path.abspath(p)) for p in sys.path)).hexdigest()
    data = "\x00".join(
        (
            _CODE_PROVENANCE_CACHE_VERSION,
            platform.python_version(),
            sys.prefix,
            main_package,
            _safe_mtime_ns(purelib),
            sys_path_hash,
            _bazel_runfiles_fingerprint(),
        )
    )
    digest = hashlib.sha256(data.encode("utf-8")).hexdigest()
    return f"{_CODE_PROVENANCE_CACHE_PREFIX}-{digest}"


def _cache_file_and_lock() -> tuple[t.Optional[Path], t.Optional[Path]]:
    try:
        base = _cache_basename()
        tmpdir = Path(tempfile.gettempdir())
        return tmpdir / f"{base}.json", tmpdir / f"{base}.lock"
    except (FileNotFoundError, OSError):
        return None, None


def _is_valid_cache_file(cache_file: Path) -> bool:
    try:
        data = cache_file.read_text(encoding="utf-8")
    except OSError:
        return False

    if not data:
        return False

    try:
        json.loads(data)
    except (TypeError, ValueError):
        return False

    return True


def _write_cached_json(cache_file: Path, data: str) -> None:
    tmp_path = cache_file.with_suffix(f"{cache_file.suffix}.{os.getpid()}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, cache_file)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def _compute_json_str() -> str:
    cp = CodeProvenance()
    return json.dumps(cp.to_dict())


def _compute_and_write(cache_file: Path) -> bool:
    computed = _compute_json_str()
    if not computed:
        return False
    try:
        _write_cached_json(cache_file, computed)
        return True
    except OSError:
        return False


def _ensure_cache_file(cache_file: Path, lock_filename: str) -> bool:
    try:
        with open(lock_filename, "a+b") as f:
            import fcntl

            try:
                fcntl.lockf(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return False

            if _is_valid_cache_file(cache_file):
                return True

            return _compute_and_write(cache_file)
    except OSError:
        return False


def get_code_provenance_file() -> t.Optional[str]:
    global _code_provenance_file_path

    if _code_provenance_file_path:
        return _code_provenance_file_path

    cache_file, lock_file = _cache_file_and_lock()
    if cache_file is None or lock_file is None:
        return None

    if _is_valid_cache_file(cache_file) or _ensure_cache_file(cache_file, str(lock_file)):
        _code_provenance_file_path = str(cache_file)
        return _code_provenance_file_path

    return None
