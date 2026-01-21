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
    except Exception:  # nosec
        pass

    try:
        platlib = Path(sysconfig.get_path("platlib"))
        if platlib.exists() and platlib not in site_packages:
            site_packages.append(platlib)
    except Exception:  # nosec
        pass

    # Add paths from sys.path that end with "site-packages" directory name only
    # This matches importlib.metadata.distributions() behavior which scans sys.path
    # but we're more conservative by only including directories named "site-packages"
    for path_str in sys.path:
        if not path_str:
            continue
        path = Path(path_str)
        if path.exists() and path.name == "site-packages" and path not in site_packages:
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
    """Find all .dist-info and .egg-info directories using importlib.metadata for discovery."""
    import importlib.metadata as importlib_metadata

    # Use importlib.metadata for discovery to ensure we match its behavior exactly
    # This avoids issues with finding packages that aren't in the working set
    try:
        for dist in importlib_metadata.distributions():
            # Get the distribution path
            if hasattr(dist, "_path"):
                dist_path = Path(dist._path)
            elif hasattr(dist, "locate_file"):
                # Try to get the path from locate_file
                try:
                    located = dist.locate_file("")
                    if located:
                        dist_path = Path(str(located))
                    else:
                        continue
                except Exception:  # nosec B112
                    # Skip distributions we can't locate - this is intentional for robustness
                    continue
            else:
                continue

            # Extract name and version from the directory name
            match = _DIST_INFO_PATTERN.match(dist_path.name)
            if match:
                name, version, _ = match.groups()
                yield name, version, dist_path
    except Exception:
        # Fallback to filesystem scanning if importlib.metadata fails
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

    # Get pkg_resources working set to filter out packages that aren't actually in the working set
    # This prevents finding transitive dependencies that aren't importable
    try:
        import pkg_resources

        # Normalize names: pkg_resources uses both hyphens and underscores, so store both forms
        working_set_names = set()
        for pkg in pkg_resources.working_set:
            name_lower = pkg.project_name.lower()
            working_set_names.add(name_lower)
            # Also add normalized forms (replace - with _ and vice versa)
            working_set_names.add(name_lower.replace("-", "_"))
            working_set_names.add(name_lower.replace("_", "-"))
    except Exception:
        # If pkg_resources fails, don't filter
        working_set_names = None

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
                    # Only include if in working set (or if we couldn't get working set)
                    if working_set_names is None or parsed_name.lower() in working_set_names:
                        distributions[parsed_name.lower()] = parsed_version
                    continue
            except (OSError, UnicodeDecodeError):
                pass

        # Fallback to directory name parsing
        if name and version:
            # Only include if in working set (or if we couldn't get working set)
            if working_set_names is None or name.lower() in working_set_names:
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
        # Cache site_packages once outside the loop to avoid repeated calls
        site_packages = _get_site_packages()
        # Cache namespace package checks to avoid repeated filesystem operations
        namespace_cache: t.Dict[str, bool] = {}

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
                        # Read all lines at once for better I/O performance
                        lines = f.readlines()

                    # Process lines more efficiently
                    seen_roots = set()
                    for line in lines:
                        # Fast check for empty lines
                        if not line or not line.strip():
                            continue

                        # RECORD format: path,hash,size
                        # Find first comma more efficiently than split
                        comma_idx = line.find(",")
                        if comma_idx <= 0:
                            continue

                        path_part = line[:comma_idx]
                        # Fast check for invalid paths
                        if not path_part or "/" not in path_part:
                            continue

                        # Use string operations instead of Path for better performance
                        # Split on "/" once to get parts
                        parts = path_part.split("/")
                        if not parts or not parts[0]:
                            continue

                        root = parts[0]
                        # Fast checks for invalid roots
                        if root.endswith((".dist-info", ".egg-info")) or root == "..":
                            continue

                        # Check for namespace packages (no __init__.py in parent)
                        # Only check if we have a second part and haven't checked this root yet
                        if len(parts) >= 2 and root not in seen_roots:
                            seen_roots.add(root)

                            # Check namespace cache first
                            is_namespace = namespace_cache.get(root)
                            if is_namespace is None:
                                # Check if it's a namespace package
                                is_namespace = False
                                for site_pkg in site_packages:
                                    root_path = site_pkg / root
                                    if root_path.is_dir() and not (root_path / "__init__.py").exists():
                                        is_namespace = True
                                        break
                                namespace_cache[root] = is_namespace

                            if is_namespace:
                                # Use first two parts for namespace package
                                root = "/".join(parts[:2])

                        # Skip if we've already mapped this root (check after namespace adjustment)
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
