import collections
from functools import lru_cache as cached
from functools import singledispatch
import logging
from pathlib import Path
import re
import sys
import sysconfig
from types import ModuleType
import typing as t

from ddtrace.internal.module import origin
from ddtrace.internal.settings.third_party import config as tp_config
from ddtrace.internal.utils.cache import callonce


LOG = logging.getLogger(__name__)

Distribution = t.NamedTuple("Distribution", [("name", str), ("version", str)])


_PACKAGE_DISTRIBUTIONS: t.Optional[t.Mapping[str, t.List[str]]] = None

# Optimized metadata reading - compiled regex patterns for better performance
_DIST_INFO_PATTERN = re.compile(r"^(.+?)-([^-]+?)\.(dist-info|egg-info)$")
_NAME_PATTERN = re.compile(r"^Name:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
_VERSION_PATTERN = re.compile(r"^Version:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)

# Cache for optimized metadata reading
_OPTIMIZED_CACHE: t.Dict[str, t.Any] = {}


def _get_site_packages() -> t.List[Path]:
    """Get all site-packages directories in the current Python environment."""
    if "site_packages" in _OPTIMIZED_CACHE:
        return _OPTIMIZED_CACHE["site_packages"]

    site_packages = []

    # Get standard library paths
    try:
        purelib = Path(sysconfig.get_path("purelib"))
        if purelib.exists():
            site_packages.append(purelib)
    except Exception:
        pass

    try:
        platlib = Path(sysconfig.get_path("platlib"))
        if platlib.exists() and platlib not in site_packages:
            site_packages.append(platlib)
    except Exception:
        pass

    # Add paths from sys.path that look like site-packages
    for path_str in sys.path:
        if not path_str:
            continue
        path = Path(path_str)
        if (
            path.exists()
            and (path.name == "site-packages" or "site-packages" in str(path))
            and path not in site_packages
        ):
            site_packages.append(path)

    _OPTIMIZED_CACHE["site_packages"] = site_packages
    return site_packages


def _parse_metadata_fast(metadata_content: str) -> t.Tuple[t.Optional[str], t.Optional[str]]:
    """Fast parsing of METADATA/PKG-INFO content to extract name and version."""
    name = version = None

    # Use regex for faster parsing instead of email.parser
    name_match = _NAME_PATTERN.search(metadata_content)
    if name_match:
        name = name_match.group(1).strip()

    version_match = _VERSION_PATTERN.search(metadata_content)
    if version_match:
        version = version_match.group(1).strip()

    return name, version


def _find_distributions_optimized() -> t.Iterator[t.Tuple[str, str, Path]]:
    """Find all .dist-info and .egg-info directories and extract package info."""
    for site_pkg in _get_site_packages():
        if not site_pkg.exists():
            continue

        try:
            for item in site_pkg.iterdir():
                if not item.is_dir():
                    continue

                # Check for .dist-info or .egg-info
                match = _DIST_INFO_PATTERN.match(item.name)
                if match:
                    name, version, _ = match.groups()
                    yield name, version, item

        except (PermissionError, OSError):
            # Skip directories we can't read
            continue


def _optimized_get_distributions() -> t.Dict[str, str]:
    """Optimized version of getting all package distributions and versions."""
    if "distributions" in _OPTIMIZED_CACHE:
        return _OPTIMIZED_CACHE["distributions"]

    distributions = {}

    for name, version, dist_path in _find_distributions_optimized():
        # Verify by reading METADATA if available
        metadata_file = dist_path / "METADATA"
        if not metadata_file.exists():
            metadata_file = dist_path / "PKG-INFO"

        if metadata_file.exists():
            try:
                with open(metadata_file, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                parsed_name, parsed_version = _parse_metadata_fast(content)
                if parsed_name and parsed_version:
                    distributions[parsed_name.lower()] = parsed_version
                    continue
            except (OSError, UnicodeDecodeError):
                pass

        # Fallback to directory name parsing
        if name and version:
            distributions[name.lower()] = version

    _OPTIMIZED_CACHE["distributions"] = distributions
    return distributions


def _optimized_packages_distributions() -> t.Mapping[str, t.List[str]]:
    """Optimized version of packages_distributions()."""
    if "packages_distributions" in _OPTIMIZED_CACHE:
        return _OPTIMIZED_CACHE["packages_distributions"]

    pkg_to_dist = collections.defaultdict(list)

    for name, version, dist_path in _find_distributions_optimized():
        # Get the actual distribution name
        dist_name = name

        # Read METADATA to get the canonical name
        metadata_file = dist_path / "METADATA"
        if not metadata_file.exists():
            metadata_file = dist_path / "PKG-INFO"

        if metadata_file.exists():
            try:
                with open(metadata_file, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                parsed_name, _ = _parse_metadata_fast(content)
                if parsed_name:
                    dist_name = parsed_name
            except (OSError, UnicodeDecodeError):
                pass

        # Get top-level packages from top_level.txt
        top_level_file = dist_path / "top_level.txt"
        top_level_packages = set()

        if top_level_file.exists():
            try:
                with open(top_level_file, "r", encoding="utf-8", errors="ignore") as f:
                    top_level_packages = {line.strip() for line in f if line.strip()}
            except (OSError, UnicodeDecodeError):
                pass

        # If no top_level.txt, try to infer from RECORD or distribution name
        if not top_level_packages:
            record_file = dist_path / "RECORD"
            if record_file.exists():
                try:
                    with open(record_file, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            if not line.strip():
                                continue
                            # RECORD format: path,hash,size
                            path_part = line.split(",")[0]
                            if "/" in path_part:
                                top_level = path_part.split("/")[0]
                                # Skip .dist-info, .egg-info, and __pycache__
                                if not (
                                    top_level.endswith((".dist-info", ".egg-info"))
                                    or top_level == "__pycache__"
                                    or top_level.endswith(".py")
                                ):
                                    top_level_packages.add(top_level)
                except (OSError, UnicodeDecodeError):
                    pass

            # Still no packages found? Use distribution name
            if not top_level_packages:
                pkg_name = dist_name.lower().replace("-", "_").replace(".", "_")
                top_level_packages.add(pkg_name)

        # Map each top-level package to this distribution
        for pkg in top_level_packages:
            pkg_to_dist[pkg].append(dist_name)

    result = dict(pkg_to_dist)
    _OPTIMIZED_CACHE["packages_distributions"] = result
    return result


@callonce
def get_distributions() -> t.Mapping[str, str]:
    """returns the mapping from distribution name to version for all distributions in a python path"""
    return _optimized_get_distributions()


def get_package_distributions() -> t.Mapping[str, t.List[str]]:
    """a mapping of importable package names to their distribution name(s)"""
    global _PACKAGE_DISTRIBUTIONS
    if _PACKAGE_DISTRIBUTIONS is None:
        _PACKAGE_DISTRIBUTIONS = _optimized_packages_distributions()
    return _PACKAGE_DISTRIBUTIONS


@cached(maxsize=1024)
def get_module_distribution_versions(module_name: str) -> t.Optional[t.Tuple[str, str]]:
    if not module_name:
        return None

    names: t.List[str] = []
    pkgs = get_package_distributions()
    dist_map = get_distributions()
    while names == []:
        # First try to resolve the module name from package distributions
        version = dist_map.get(module_name)
        if version:
            return (module_name, version)
        # Since we've failed to resolve, try to resolve the parent package
        names = pkgs.get(module_name, [])
        if not names:
            p = module_name.rfind(".")
            if p > 0:
                module_name = module_name[:p]
            else:
                break
    if len(names) != 1:
        # either it was not resolved due to multiple packages with the same name
        # or it's a multipurpose package (like '__pycache__')
        return None
    return (names[0], get_version_for_package(names[0]))


@cached(maxsize=1024)
def get_version_for_package(name: str) -> str:
    """returns the version of a package"""
    try:
        distributions = get_distributions()
        return distributions.get(name.lower(), "")
    except Exception:
        return ""


def _effective_root(rel_path: Path, parent: Path) -> str:
    base = rel_path.parts[0]
    root = parent / base
    return base if root.is_dir() and (root / "__init__.py").exists() else "/".join(rel_path.parts[:2])


# DEV: Since we can't lock on sys.path, these operations can be racy.
_SYS_PATH_HASH: t.Optional[int] = None
_RESOLVED_SYS_PATH: t.List[Path] = []


def resolve_sys_path() -> t.List[Path]:
    global _SYS_PATH_HASH, _RESOLVED_SYS_PATH

    if (h := hash(tuple(sys.path))) != _SYS_PATH_HASH:
        _SYS_PATH_HASH = h
        _RESOLVED_SYS_PATH = [Path(_).resolve() for _ in sys.path]

    return _RESOLVED_SYS_PATH


def _root_module(path: Path) -> str:
    # Try the most likely prefixes first
    for parent_path in (purelib_path, platlib_path):
        try:
            # Resolve the path to use the shortest relative path.
            return _effective_root(path.resolve().relative_to(parent_path), parent_path)
        except ValueError:
            # Not relative to this path
            pass

    # Try to resolve the root module using sys.path. We keep the shortest
    # relative path as the one more likely to give us the root module.
    min_relative_path = max_parent_path = None
    for parent_path in resolve_sys_path():
        try:
            relative = path.relative_to(parent_path)
            if min_relative_path is None or len(relative.parents) < len(min_relative_path.parents):
                min_relative_path, max_parent_path = relative, parent_path
        except ValueError:
            pass

    if min_relative_path is not None:
        try:
            return _effective_root(min_relative_path, t.cast(Path, max_parent_path))
        except IndexError:
            pass

    # Bazel runfiles support: we assume that these paths look like
    # /some/path.runfiles/<distribution_name>/site-packages/<root_module>/...
    # /usr/local/runfiles/<distribution_name>/site-packages/<root_module>/...
    for s in path.parents:
        if s.parent.name == "site-packages":
            return s.name

    msg = f"Could not find root module for path {path}"
    raise ValueError(msg)


@callonce
def _package_for_root_module_mapping() -> t.Optional[t.Dict[str, Distribution]]:
    try:
        mapping = {}

        for name, version, dist_path in _find_distributions_optimized():
            # Get the actual distribution name and version from metadata if available
            dist_name, dist_version = name, version
            metadata_file = dist_path / "METADATA"
            if not metadata_file.exists():
                metadata_file = dist_path / "PKG-INFO"

            if metadata_file.exists():
                try:
                    with open(metadata_file, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    parsed_name, parsed_version = _parse_metadata_fast(content)
                    if parsed_name and parsed_version:
                        dist_name, dist_version = parsed_name, parsed_version
                except (OSError, UnicodeDecodeError):
                    pass

            d = Distribution(name=dist_name, version=dist_version)

            # Try to read RECORD file to get file mappings
            record_file = dist_path / "RECORD"
            if record_file.exists():
                try:
                    with open(record_file, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            if not line.strip():
                                continue
                            # RECORD format: path,hash,size
                            path_part = line.split(",")[0]
                            if not path_part or "/" not in path_part:
                                continue

                            parts = Path(path_part).parts
                            root = parts[0]
                            if root.endswith((".dist-info", ".egg-info")) or root == "..":
                                continue

                            # Check for namespace packages (no __init__.py in parent)
                            if len(parts) >= 2:
                                # This is a simplified namespace check
                                # If we can't find an __init__.py in site-packages/root/, assume namespace
                                for site_pkg in _get_site_packages():
                                    root_path = site_pkg / root
                                    if root_path.is_dir() and not (root_path / "__init__.py").exists():
                                        root = "/".join(parts[:2])
                                        break

                            if root not in mapping:
                                mapping[root] = d
                except (OSError, UnicodeDecodeError):
                    pass
            else:
                # No RECORD file, use distribution name as fallback
                pkg_name = dist_name.lower().replace("-", "_").replace(".", "_")
                if pkg_name not in mapping:
                    mapping[pkg_name] = d

        return mapping

    except Exception:
        LOG.warning(
            "Unable to build package file mapping, please report this to https://github.com/DataDog/dd-trace-py/issues",
            exc_info=True,
        )
        return None


@callonce
def _third_party_packages() -> set:
    from gzip import decompress
    from importlib.resources import read_binary

    return (
        set(decompress(read_binary("ddtrace.internal", "third-party.tar.gz")).decode("utf-8").splitlines())
        | tp_config.includes
    ) - tp_config.excludes


@cached(maxsize=16384)
def filename_to_package(filename: t.Union[str, Path]) -> t.Optional[Distribution]:
    mapping = _package_for_root_module_mapping()
    if mapping is None:
        return None

    try:
        path = Path(filename) if isinstance(filename, str) else filename
        # Avoid calling .resolve() on the path here to prevent breaking symlink matching in `_root_module`.
        root_module_path = _root_module(path)
        if root_module_path in mapping:
            return mapping[root_module_path]

        # Loop through mapping and check the distribution name, since the key isn't always the same, for example:
        #   '__editable__.ddtrace-3.9.0.dev...pth': Distribution(name='ddtrace', version='...')
        for distribution in mapping.values():
            if distribution.name == root_module_path:
                return distribution

        return None
    except (ValueError, OSError):
        return None


@cached(maxsize=256)
def module_to_package(module: ModuleType) -> t.Optional[Distribution]:
    """Returns the package distribution for a module"""
    module_origin = origin(module)
    return filename_to_package(module_origin) if module_origin is not None else None


stdlib_path = Path(sysconfig.get_path("stdlib")).resolve()
platstdlib_path = Path(sysconfig.get_path("platstdlib")).resolve()
purelib_path = Path(sysconfig.get_path("purelib")).resolve()
platlib_path = Path(sysconfig.get_path("platlib")).resolve()


@cached(maxsize=256)
def is_stdlib(path: Path) -> bool:
    rpath = path
    if not rpath.is_absolute() or rpath.is_symlink():
        rpath = rpath.resolve()

    return (rpath.is_relative_to(stdlib_path) or rpath.is_relative_to(platstdlib_path)) and not (
        rpath.is_relative_to(purelib_path) or rpath.is_relative_to(platlib_path)
    )


@cached(maxsize=256)
def is_third_party(path: Path) -> bool:
    package = filename_to_package(path)
    if package is None:
        return False

    return package.name in _third_party_packages()


@singledispatch
def is_user_code(path) -> bool:
    raise NotImplementedError(f"Unsupported type {type(path)}")


@is_user_code.register
def _(path: Path) -> bool:
    return not (is_stdlib(path) or is_third_party(path))


# DEV: Creating Path objects on Python < 3.11 is expensive
@is_user_code.register(str)
@cached(maxsize=1024)
def _(path: str) -> bool:
    _path = Path(path)
    return not (is_stdlib(_path) or is_third_party(_path))


@cached(maxsize=256)
def is_distribution_available(name: str) -> bool:
    """Determine if a distribution is available in the current environment."""
    try:
        distributions = get_distributions()
        return name.lower() in distributions
    except Exception:
        return False


# Note: Old importlib_metadata helper functions removed - we now use optimized implementations above
