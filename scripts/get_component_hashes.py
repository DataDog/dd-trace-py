#!/usr/bin/env python3
"""
Generate component-specific hashes for build caching.

This script creates separate hashes for different build components:
- CMake extensions (IAST, DDUP, crashtracker, stack_v2)
- Rust extensions
- Cython extensions
- Regular C/C++ extensions
- Python source files that affect builds

Each component can be cached independently, allowing for partial rebuilds
when only some components have changed.
"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Dict
from typing import List
from typing import Set


# Try to import the component config
try:
    from .component_config import ComponentConfig
except ImportError:
    # Fallback for when used as standalone script
    from component_config import ComponentConfig


def get_file_hash(filepath: Path) -> str:
    """Get SHA256 hash of a file."""
    try:
        with open(filepath, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except (OSError, IOError):
        return ""


def get_files_hash(file_patterns: List[str], base_path: Path = None) -> str:
    """Get combined hash of multiple files/patterns."""
    if base_path is None:
        base_path = Path.cwd()

    files = []
    for pattern in file_patterns:
        if "*" in pattern or "?" in pattern:
            # Use glob pattern
            files.extend(base_path.glob(pattern))
        else:
            # Direct file path
            file_path = base_path / pattern
            if file_path.exists():
                files.append(file_path)

    # Sort files for consistent hashing
    files = sorted(files)

    hasher = hashlib.sha256()
    for file_path in files:
        if file_path.is_file():
            hasher.update(str(file_path.relative_to(base_path)).encode())
            hasher.update(get_file_hash(file_path).encode())

    return hasher.hexdigest()


def get_pyenv_versions_hash() -> str:
    """Get hash of installed Python versions."""
    try:
        result = subprocess.run(["pyenv", "versions"], capture_output=True, text=True, check=True)
        versions = sorted(result.stdout.strip().split("\n"))
        return hashlib.sha256("\n".join(versions).encode()).hexdigest()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return hashlib.sha256(f"python-{sys.version}".encode()).hexdigest()


def generate_component_hashes(base_path: Path = None) -> Dict[str, str]:
    """Generate hashes for each build component."""
    if base_path is None:
        base_path = Path.cwd()

    # Common files that affect all builds
    common_files = [
        "setup.py",
        "pyproject.toml",
        "hatch.toml",
    ]

    # Get component definitions from centralized config
    # Convert simple paths to glob patterns for file discovery
    components = {}
    for component in ComponentConfig.get_all_components():
        paths = ComponentConfig.get_component_paths(component)
        patterns = []
        for path in paths:
            if path.endswith("/"):
                # Directory - add common file patterns
                patterns.extend(
                    [
                        f"{path}**/*.c",
                        f"{path}**/*.cpp",
                        f"{path}**/*.h",
                        f"{path}**/*.pyx",
                        f"{path}**/*.rs",
                        f"{path}**/CMakeLists.txt",
                        f"{path}**/Cargo.*",
                        f"{path}**/setup.py",
                    ]
                )
            else:
                # Specific file
                patterns.append(path)
        components[component] = patterns

    hashes = {}

    # Get pyenv versions hash (affects all components)
    pyenv_hash = get_pyenv_versions_hash()

    # Get common files hash (affects all components)
    common_hash = get_files_hash(common_files, base_path)

    # Generate hash for each component
    for component_name, file_patterns in components.items():
        # Combine component-specific files with common files and pyenv
        component_files = []
        for pattern in file_patterns:
            if "**" in pattern:
                # Recursive glob
                component_files.extend(base_path.rglob(pattern.replace("**/", "")))
            elif "*" in pattern:
                # Simple glob
                component_files.extend(base_path.glob(pattern))
            else:
                # Direct file
                file_path = base_path / pattern
                if file_path.exists():
                    component_files.append(file_path)

        # Calculate hash for this component
        component_hasher = hashlib.sha256()
        component_hasher.update(common_hash.encode())
        component_hasher.update(pyenv_hash.encode())

        # Sort files for consistent hashing
        component_files = sorted(set(component_files))
        for file_path in component_files:
            if file_path.is_file():
                component_hasher.update(str(file_path.relative_to(base_path)).encode())
                component_hasher.update(get_file_hash(file_path).encode())

        hashes[component_name] = component_hasher.hexdigest()[:16]  # Use first 16 chars

    # Also generate the combined hash for backward compatibility
    all_hasher = hashlib.sha256()
    for component_hash in sorted(hashes.values()):
        all_hasher.update(component_hash.encode())
    hashes["combined"] = all_hasher.hexdigest()[:16]

    return hashes


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Generate component-specific build hashes")
    parser.add_argument(
        "--format", choices=["json", "env", "shell"], default="env", help="Output format (default: env)"
    )
    parser.add_argument("--component", help="Get hash for specific component only")
    parser.add_argument("--base-path", type=Path, help="Base path for file resolution")

    args = parser.parse_args()

    hashes = generate_component_hashes(args.base_path)

    if args.component:
        if args.component in hashes:
            print(hashes[args.component])
        else:
            print(f"Unknown component: {args.component}", file=sys.stderr)
            print(f"Available components: {', '.join(sorted(hashes.keys()))}", file=sys.stderr)
            sys.exit(1)
    elif args.format == "json":
        print(json.dumps(hashes, indent=2))
    elif args.format == "shell":
        for component, hash_value in sorted(hashes.items()):
            print(f"export DD_{component.upper()}_HASH={hash_value}")
    else:  # env format
        for component, hash_value in sorted(hashes.items()):
            print(f"DD_{component.upper()}_HASH={hash_value}")


if __name__ == "__main__":
    main()
