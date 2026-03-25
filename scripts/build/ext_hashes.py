"""Compute source-file hashes for all compiled ddtrace extensions.

Source discovery
----------------
All extension metadata is derived from existing declarations — no source paths
are duplicated here:

* **Cython** extensions are discovered from ``*.pyx`` files under ``ddtrace/``.
  The install path and module name are derived from the file's location in the
  package tree.

* **C / C++** extensions are discovered by parsing ``CMakeLists.txt`` files
  for ``python3_add_library`` and ``add_library`` calls, together with their
  associated ``install()`` and ``set_target_properties()`` declarations.  The
  cmake files are the single source of truth; adding a new extension or source
  file to a cmake file automatically changes the hash.

* **Rust** extension sources are taken from ``src/native/`` (all files
  excluding the cargo ``target/`` output directory).

For each extension, all source files in the extension's source directory are
hashed.  ``EXT_SUFFIX`` is mixed into every hash so that binaries compiled
against different Python versions — which may call different CPython C-API
functions — are stored in separate cache buckets.

Usage
-----
    python scripts/build/ext_hashes.py

Environment variables
---------------------
DD_COMPILE_MODE
    Build mode used to locate the CMake binary tree (default: RelWithDebInfo).
DD_PY_LIMITED_API
    When "1" / "yes" / "on" / "true", the Rust extension is assumed to use the
    stable ABI suffix (``.abi3.so`` / ``.pyd``).  Default is "0".
DD_SERVERLESS_BUILD
    When "1" / "yes" / "on" / "true", serverless feature flags are used for
    the Rust extension hash (mirrors CMakeLists.txt feature-selection logic).
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import platform
import re
import struct
import sys
import sysconfig
from typing import Iterator


HERE = Path(__file__).resolve().parent.parent.parent

# ── Global constants ──────────────────────────────────────────────────────────

EXT_SUFFIX: str = sysconfig.get_config_var("EXT_SUFFIX") or ".so"

_PY_MAJOR = sys.version_info.major
_PY_MINOR = sys.version_info.minor

_IS_WIN32 = sys.platform == "win32"


def _bool_env(name: str, default: bool = True) -> bool:
    val = os.getenv(name, "1" if default else "0").lower()
    return val in ("1", "yes", "on", "true")


DD_PY_LIMITED_API: bool = _bool_env("DD_PY_LIMITED_API", default=False)
COMPILE_MODE: str = os.getenv("DD_COMPILE_MODE", "RelWithDebInfo")

if DD_PY_LIMITED_API:
    _NATIVE_SUFFIX = ".pyd" if _IS_WIN32 else ".abi3.so"
else:
    _NATIVE_SUFFIX = EXT_SUFFIX


# ── File helpers ───────────────────────────────────────────────────────────────


# Directories and suffixes that are never part of the source being hashed.
# These mirror what .gitignore would exclude: build outputs, caches, etc.
_PRUNE_DIRS = frozenset({"__pycache__", ".git", "target", "build", ".eggs", ".tox", "dist"})
_PRUNE_SUFFIXES = frozenset({".pyc", ".pyo", ".so", ".pyd", ".dylib", ".egg-info"})


def _walk_sources(*dirs: str) -> list[Path]:
    """Return all source files under the given repo-relative paths.

    Prunes known non-source directories (build outputs, caches, Cargo target
    directories) and compiled-file suffixes so the result is stable regardless
    of whether a build has run.
    """
    result: list[Path] = []
    for d in dirs:
        root = HERE / d
        if root.is_file():
            result.append(root)
            continue
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [dn for dn in dirnames if dn not in _PRUNE_DIRS and not dn.startswith(".")]
            for fname in filenames:
                p = Path(dirpath) / fname
                if p.suffix not in _PRUNE_SUFFIXES:
                    result.append(p)
    return result


def _hash_files(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.read_bytes())
    return h.hexdigest()


def _versioned_hash(*parts: str) -> str:
    """Combine arbitrary string parts into a single SHA-256 digest.

    Always include ``EXT_SUFFIX`` so binaries for different Python versions
    land in separate cache directories.
    """
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode())
    return h.hexdigest()


def _emit(module_name: str, digest: str, rel_target: str) -> None:
    print("#EXTHASH:", (module_name, digest, str(HERE / rel_target)))


# ── Extension info ─────────────────────────────────────────────────────────────


@dataclass
class _ExtInfo:
    """Metadata for one compiled extension."""

    module_name: str  # dotted Python import path  e.g. ddtrace.appsec._iast._stacktrace
    target_path: str  # repo-relative path to the .so / .pyd artifact
    source_dirs: list[str]  # repo-relative directories to hash


def _emit_ext(info: _ExtInfo) -> None:
    sources = _walk_sources(*info.source_dirs)
    digest = _versioned_hash(_hash_files(sources), EXT_SUFFIX)
    _emit(info.module_name, digest, info.target_path)


# ── Minimal cmake parser ───────────────────────────────────────────────────────
#
# Parses the small subset of cmake syntax used in this repo to declare Python
# extensions:  set(), file(GLOB), python3_add_library(), add_library(),
# install(), set_target_properties(), and add_subdirectory().
#
# Design goals:
#   - Linear (document-order) processing so that variable assignments that
#     precede add_subdirectory() calls are visible in the child scope.
#   - Does NOT attempt to evaluate cmake conditionals — all branches are
#     processed.  This is intentionally conservative: it may discover
#     platform-specific extensions on other platforms, which causes harmless
#     extra cache misses rather than silent misses.


def _strip_comments(text: str) -> str:
    return re.sub(r"(?m)#[^\n]*", "", text)


def _iter_calls(content: str) -> Iterator[tuple[str, str]]:
    """Yield ``(lowercase_func_name, args_str)`` for each cmake function call.

    Nested calls (inside the argument list of another call) are *skipped*:
    after yielding a call the scan resumes past its closing parenthesis.
    """
    pattern = re.compile(r"\b(\w+)\s*\(", re.IGNORECASE)
    pos = 0
    while pos < len(content):
        m = pattern.search(content, pos)
        if not m:
            break
        func = m.group(1).lower()
        depth = 1
        i = m.end()
        while i < len(content) and depth > 0:
            if content[i] == "(":
                depth += 1
            elif content[i] == ")":
                depth -= 1
            i += 1
        if depth == 0:
            yield func, content[m.end() : i - 1]
        pos = i  # skip nested calls inside this one


def _expand(text: str, variables: dict[str, str]) -> str:
    """Expand ``${VAR}`` references up to 5 times (handles chained expansions)."""
    for _ in range(5):
        new = re.sub(r"\$\{(\w+)\}", lambda m: variables.get(m.group(1), ""), text)
        if new == text:
            break
        text = new
    return text


def _tokenize(text: str) -> list[str]:
    """Split a cmake argument string into tokens, respecting quoted strings."""
    tokens: list[str] = []
    i = 0
    while i < len(text):
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            break
        if text[i] == '"':
            j = i + 1
            while j < len(text) and text[j] != '"':
                j += 1
            tokens.append(text[i + 1 : j])
            i = j + 1
        else:
            j = i
            while j < len(text) and not text[j].isspace():
                j += 1
            tokens.append(text[i:j])
            i = j
    return [t for t in tokens if t]


def _args(args_str: str, variables: dict[str, str]) -> list[str]:
    return _tokenize(_expand(args_str, variables))


# cmake keywords that delimit value lists inside install() / set_target_properties()
_CMAKE_INSTALL_KW = frozenset(
    "ARCHIVE RUNTIME LIBRARY DESTINATION PERMISSIONS CONFIGURATIONS "
    "COMPONENT NAMELINK_COMPONENT OPTIONAL EXCLUDE_FROM_ALL TARGETS".split()
)


def _cmake_discover(cmake_file: Path, parent_vars: dict[str, str] | None = None) -> list[_ExtInfo]:
    """Parse *cmake_file* and return all Python extensions declared in it.

    Variables from *parent_vars* (set by the parent cmake before
    ``add_subdirectory``) are inherited into the child scope.
    """
    raw = cmake_file.read_text(errors="replace")
    content = _strip_comments(raw)
    cmake_dir = cmake_file.parent

    variables: dict[str, str] = {
        "CMAKE_CURRENT_SOURCE_DIR": str(cmake_dir.relative_to(HERE)),
        "CMAKE_CURRENT_LIST_DIR": str(cmake_dir.relative_to(HERE)),
        "CMAKE_SOURCE_DIR": "",  # repo root; relative paths from here are correct
        # Pre-populate the cmake variable names that equal sysconfig values so
        # that EXTENSION_NAME="_foo${PYTHON_EXT_SUFFIX}" expands correctly.
        "PYTHON_EXT_SUFFIX": EXT_SUFFIX,
        "EXTENSION_SUFFIX": EXT_SUFFIX,
    }
    if parent_vars:
        variables.update(parent_vars)

    # Accumulate target metadata incrementally as we scan through the file.
    # Maps expanded-target-name → mutable dict with keys: install_dest, output_name, source_dir.
    pending: dict[str, dict] = {}
    extensions: list[_ExtInfo] = []

    for func, args_str in _iter_calls(content):
        if func == "set":
            toks = _tokenize(args_str)  # expand *after* extracting variable name
            if len(toks) < 2:
                continue
            var_name = toks[0]
            # Collect value tokens; stop at CACHE or PARENT_SCOPE.
            value_parts: list[str] = []
            for tok in toks[1:]:
                if tok.upper() in ("CACHE", "PARENT_SCOPE", "FORCE"):
                    break
                value_parts.append(tok)
            variables[var_name] = _expand(" ".join(value_parts), variables)

        elif func == "file":
            toks = _args(args_str, variables)
            if len(toks) >= 3 and toks[0].upper() in ("GLOB", "GLOB_RECURSE"):
                var_name = toks[1]
                matched: list[str] = []
                for pat in toks[2:]:
                    p_pat = Path(pat)
                    if p_pat.is_absolute():
                        try:
                            rel = p_pat.relative_to(HERE)
                        except ValueError:
                            continue  # path outside repo root — skip
                    else:
                        rel = p_pat
                    for p in HERE.glob(str(rel)):
                        if p.is_file():
                            try:
                                matched.append(str(p.relative_to(HERE)))
                            except ValueError:
                                pass
                variables[var_name] = " ".join(matched)

        elif func in ("python3_add_library", "add_library"):
            toks = _args(args_str, variables)
            if len(toks) < 2:
                continue
            raw_name = toks[0]  # may still contain unexpanded parts? No — _args expands.
            lib_type = toks[1].upper() if len(toks) > 1 else ""
            if lib_type not in ("MODULE", "SHARED"):
                continue

            # Cython extensions that use add_library compile a .pyx-generated
            # .cpp into the target.  They are already discovered via the .pyx
            # auto-detection path; skip them here to avoid duplicate entries.
            # Heuristic: the cmake file's directory contains a .pyx file.
            if func == "add_library" and any(cmake_dir.glob("*.pyx")):
                continue

            source_dir = str(cmake_dir.relative_to(HERE))

            if raw_name == "dd_wrapper":
                # dd_wrapper uses a special output-file naming scheme derived
                # from EXTENSION_SUFFIX and its install destination comes from
                # LIB_INSTALL_DIR set by the *parent* cmake (before the child
                # cmake unconditionally sets LIB_INSTALL_DIR to "").  Bypass
                # the pending dict entirely so that subsequent install() and
                # set_target_properties() processing can't corrupt the values.
                lib_install = variables.get("LIB_INSTALL_DIR", "")
                if lib_install:
                    ext_no_so = re.sub(r"\.so$", "", EXT_SUFFIX)
                    output_file = f"dd_wrapper{ext_no_so}.so"
                    extensions.append(
                        _ExtInfo(
                            module_name=lib_install.replace("/", ".") + ".dd_wrapper",
                            target_path=f"{lib_install}/{output_file}",
                            source_dirs=[source_dir],
                        )
                    )
            else:
                # For ${EXTENSION_NAME} targets (e.g. _stack.cpython-313-…)
                # the expanded name already includes the EXT_SUFFIX.
                stem = raw_name.split(".")[0]
                pending[raw_name] = {
                    "output_file": raw_name,  # expanded name IS the filename
                    "stem": stem,
                    "source_dir": source_dir,
                }

        elif func == "install":
            toks = _args(args_str, variables)
            if "TARGETS" not in (t.upper() for t in toks):
                continue
            targets_idx = next((i for i, t in enumerate(toks) if t.upper() == "TARGETS"), -1)
            listed: list[str] = []
            for tok in toks[targets_idx + 1 :]:
                if tok.upper() in _CMAKE_INSTALL_KW:
                    break
                listed.append(tok)
            try:
                dest_idx = next(i for i, t in enumerate(toks) if t.upper() == "DESTINATION")
                dest = toks[dest_idx + 1]
            except (StopIteration, IndexError):
                continue
            for name in listed:
                if name in pending:
                    pending[name]["install_dest"] = dest

        elif func == "set_target_properties":
            toks = _args(args_str, variables)
            if not toks:
                continue
            target = toks[0]
            if target in pending:
                try:
                    idx = next(i for i, t in enumerate(toks) if t.upper() == "OUTPUT_NAME")
                    pending[target]["output_name_override"] = toks[idx + 1]
                except (StopIteration, IndexError):
                    pass

        elif func == "add_subdirectory":
            toks = _args(args_str, variables)
            if not toks:
                continue
            subdir_rel = toks[0]
            subdir_abs = cmake_dir / subdir_rel
            subdir_cmake = subdir_abs / "CMakeLists.txt"
            if subdir_cmake.exists() and subdir_cmake != cmake_file:
                child = _cmake_discover(subdir_cmake, parent_vars=dict(variables))
                extensions.extend(child)

    # Finalise pending targets that have a resolved install destination.
    for _, info in pending.items():
        install_dest: str | None = info.get("install_dest")
        if not install_dest:
            continue
        output_file = info.get("output_name_override", info["output_file"])
        # Append EXT_SUFFIX for python3_add_library targets whose OUTPUT_NAME
        # doesn't already end with it (it is set via SUFFIX="${PYTHON_EXT_SUFFIX}").
        if not output_file.endswith(EXT_SUFFIX) and not output_file.endswith(".so"):
            output_file = output_file + EXT_SUFFIX
        # Derive the module stem from the actual output filename, not the cmake
        # target name — they can differ (e.g. iast_native → _native, psutil_posix → _psutil_posix).
        stem = output_file.split(".")[0]
        module_name = install_dest.replace("/", ".") + "." + stem
        target_path = f"{install_dest}/{output_file}"
        source_dir = info["source_dir"]
        # For python3_add_library targets the source_dir equals the install dest
        # (sources live alongside the installed .so).  For add_library targets in
        # their own cmake subdirectory it is the cmake file's own directory.
        extensions.append(_ExtInfo(module_name=module_name, target_path=target_path, source_dirs=[source_dir]))

    return extensions


# ── Cython extension discovery ────────────────────────────────────────────────


def _discover_cython() -> list[_ExtInfo]:
    """Auto-discover Cython extensions from all ``.pyx`` files under ``ddtrace/``."""
    extensions: list[_ExtInfo] = []
    for pyx_path in _walk_sources("ddtrace"):
        if pyx_path.suffix != ".pyx":
            continue
        rel = pyx_path.relative_to(HERE)
        package_dir = str(rel.parent)
        module_name = package_dir.replace("/", ".") + "." + rel.stem
        target_path = f"{package_dir}/{rel.stem}{EXT_SUFFIX}"
        extensions.append(_ExtInfo(module_name=module_name, target_path=target_path, source_dirs=[package_dir]))
    return extensions


# ── Rust extension ─────────────────────────────────────────────────────────────


def _rust_features() -> list[str]:
    """Mirrors the Rust feature-selection logic in the top-level CMakeLists.txt."""
    feats = ["stats"]
    is_64 = struct.calcsize("P") == 8
    system = platform.system()
    serverless = _bool_env("DD_SERVERLESS_BUILD", default=False)
    if (system in ("Linux", "Darwin")) and is_64:
        feats.append("profiling")
        if not serverless:
            feats.append("crashtracker")
    if not serverless:
        feats.append("ffe")
    if DD_PY_LIMITED_API:
        feats.append("abi3")
    return feats


def _wheel_tag() -> str:
    pt = f"cp{_PY_MAJOR}{_PY_MINOR}"
    at = f"{pt}t" if getattr(sys, "_is_gil_disabled", False) else pt
    plat = sysconfig.get_platform().replace("-", "_").replace(".", "_")
    return f"{pt}-{at}-{plat}"


def _rust_native() -> None:
    """Emit hashes for the Rust ``_native`` extension and its generated C headers."""
    sources = _walk_sources("src/native")
    # Mix in features and EXT_SUFFIX: pyo3 uses version-gated CPython APIs
    # (e.g. PyType_GetQualName added in 3.11), so the binary differs across
    # Python versions even when Rust sources are identical.
    digest = _versioned_hash(_hash_files(sources), *_rust_features(), _NATIVE_SUFFIX)
    _emit("ddtrace.internal.native._native", digest, f"ddtrace/internal/native/_native{_NATIVE_SUFFIX}")

    # Rust-generated C headers (consumed by _memalloc / dd_wrapper).
    headers_dir = HERE / "build" / f"cmake-{_wheel_tag()}" / "cargo" / "build" / "include" / "datadog"
    if headers_dir.is_dir():
        for h_file in sorted(headers_dir.glob("*.h")):
            _emit("ddtrace.internal.native._native", digest, str(h_file.relative_to(HERE)))


# ── Main ───────────────────────────────────────────────────────────────────────


def main() -> None:
    # Rust extension
    _rust_native()

    # C/C++ extensions — discovered by parsing all cmake declarations.
    # Start from the top-level CMakeLists.txt so that variable scope
    # (EXTENSION_NAME, LIB_INSTALL_DIR, …) is inherited correctly into
    # subdirectory cmake files via add_subdirectory().
    cmake_extensions = _cmake_discover(HERE / "CMakeLists.txt")

    # Cython extensions — discovered from git-tracked .pyx files.
    cython_extensions = _discover_cython()

    # Merge: cmake may also discover Cython targets (via add_library for the
    # Cython-generated .cpp).  The .pyx-based entries are more accurate (they
    # point to the .pyx source directory), so they win on ties.
    seen_targets: set[str] = set()
    all_extensions: list[_ExtInfo] = []

    # Cython entries first so they take priority during deduplication.
    for info in cython_extensions + cmake_extensions:
        if info.target_path not in seen_targets:
            seen_targets.add(info.target_path)
            all_extensions.append(info)

    for info in all_extensions:
        _emit_ext(info)


if __name__ == "__main__":
    main()
