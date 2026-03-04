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
from ddtrace.internal.packages import _package_for_root_module_mapping


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

        module_to_distribution: dict[str, Distribution] = _package_for_root_module_mapping() or {}

        libraries: dict[str, Library] = {}

        site_packages = Path(sysconfig.get_path("purelib"))
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
            if module.endswith(".py") or module_path.is_dir():
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


def _cache_basename() -> str:
    purelib = sysconfig.get_path("purelib")
    main_package = os.getenv("DD_MAIN_PACKAGE", "")
    data = "\x00".join(
        (
            _CODE_PROVENANCE_CACHE_VERSION,
            platform.python_version(),
            sys.prefix,
            main_package,
            _safe_mtime_ns(purelib),
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
