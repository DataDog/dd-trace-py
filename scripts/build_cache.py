#!/usr/bin/env python3
"""
Build cache management utilities for ddtrace.

This module provides utilities for managing build caches at different levels:
- Local build cache (for incremental builds)
- Component-specific caches (for partial rebuilds)
- Download cache (for external dependencies)

The cache system is designed to work with both local development and CI environments.
"""

import json
import os
from pathlib import Path
import shutil
import time
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple


# Try to import the component hash generator and config
try:
    from .component_config import ComponentConfig
    from .get_component_hashes import generate_component_hashes
except ImportError:
    # Fallback for when used as standalone script
    from component_config import ComponentConfig
    from get_component_hashes import generate_component_hashes


class BuildCache:
    """Manages build caches for different components."""

    def __init__(self, base_dir: Optional[Path] = None):
        """Initialize build cache manager.

        Args:
            base_dir: Base directory for the project (defaults to current working directory)
        """
        import sys

        self.base_dir = base_dir or Path.cwd()
        self.cache_dir = self.base_dir / ".build_cache"
        self.download_cache_dir = self.base_dir / ".download_cache"

        # Python version string for cache separation
        self.python_version = f"{sys.version_info.major}.{sys.version_info.minor}"

        # Component cache directories - separated by Python version for compiled artifacts
        # Download cache is shared since external libraries are usually not Python-specific
        self.component_cache_dirs = {
            component: self.cache_dir
            / f"py{self.python_version}"
            / ComponentConfig.get_component_cache_dir_name(component)
            for component in ComponentConfig.get_all_components()
        }

        self._component_hashes: Optional[Dict[str, str]] = None

    def ensure_cache_dirs(self) -> None:
        """Ensure all cache directories exist."""
        self.cache_dir.mkdir(exist_ok=True)
        self.download_cache_dir.mkdir(exist_ok=True)
        for cache_dir in self.component_cache_dirs.values():
            cache_dir.mkdir(parents=True, exist_ok=True)

    def get_component_hashes(self) -> Dict[str, str]:
        """Get component hashes, caching the result."""
        if self._component_hashes is None:
            self._component_hashes = generate_component_hashes(self.base_dir)
        return self._component_hashes

    def get_cache_info_path(self, component: str) -> Path:
        """Get path to cache info file for a component."""
        return self.component_cache_dirs[component] / "cache_info.json"

    def get_cached_artifacts_path(self, component: str) -> Path:
        """Get path to cached artifacts directory for a component."""
        return self.component_cache_dirs[component] / "artifacts"

    def save_cache_info(self, component: str, info: Dict) -> None:
        """Save cache information for a component."""
        cache_info_path = self.get_cache_info_path(component)
        cache_info_path.parent.mkdir(parents=True, exist_ok=True)

        with open(cache_info_path, "w") as f:
            json.dump(info, f, indent=2)

    def load_cache_info(self, component: str) -> Optional[Dict]:
        """Load cache information for a component."""
        cache_info_path = self.get_cache_info_path(component)
        if not cache_info_path.exists():
            return None

        try:
            with open(cache_info_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def is_cache_valid(self, component: str) -> bool:
        """Check if cache is valid for a component."""
        cache_info = self.load_cache_info(component)
        if not cache_info:
            return False

        # Check if Python version matches
        cached_python_version = cache_info.get("additional_info", {}).get("python_version")
        if cached_python_version and cached_python_version != self.python_version:
            return False

        current_hashes = self.get_component_hashes()
        cached_hash = cache_info.get("hash")
        current_hash = current_hashes.get(component)

        return cached_hash == current_hash

    def cache_artifacts(self, component: str, source_paths: List[Path], additional_info: Optional[Dict] = None) -> bool:
        """Cache artifacts for a component.

        Args:
            component: Component name
            source_paths: Paths to files/directories to cache
            additional_info: Additional information to store in cache

        Returns:
            True if caching succeeded, False otherwise
        """
        try:
            artifacts_dir = self.get_cached_artifacts_path(component)
            artifacts_dir.mkdir(parents=True, exist_ok=True)

            # Clear existing cached artifacts
            if artifacts_dir.exists():
                shutil.rmtree(artifacts_dir)
            artifacts_dir.mkdir(parents=True, exist_ok=True)

            # Cache each source path
            cached_paths = []
            for source_path in source_paths:
                if not source_path.exists():
                    continue

                if source_path.is_file():
                    dest_path = artifacts_dir / source_path.name
                    shutil.copy2(source_path, dest_path)
                    cached_paths.append(str(dest_path.relative_to(artifacts_dir)))
                elif source_path.is_dir():
                    dest_path = artifacts_dir / source_path.name
                    shutil.copytree(source_path, dest_path, dirs_exist_ok=True)
                    cached_paths.append(str(dest_path.relative_to(artifacts_dir)))

            # Save cache info
            current_hashes = self.get_component_hashes()
            cache_info = {
                "hash": current_hashes.get(component),
                "timestamp": time.time(),
                "cached_paths": cached_paths,
                "additional_info": additional_info or {},
            }
            self.save_cache_info(component, cache_info)

            return True
        except Exception as e:
            print(f"WARNING: Failed to cache artifacts for {component}: {e}")
            return False

    def restore_artifacts(self, component: str, dest_dir: Path) -> bool:
        """Restore cached artifacts for a component.

        Args:
            component: Component name
            dest_dir: Destination directory to restore artifacts to

        Returns:
            True if restoration succeeded, False otherwise
        """
        if not self.is_cache_valid(component):
            return False

        try:
            artifacts_dir = self.get_cached_artifacts_path(component)
            if not artifacts_dir.exists():
                return False

            # Restore cached artifacts
            for item in artifacts_dir.iterdir():
                dest_path = dest_dir / item.name
                if item.is_file():
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, dest_path)
                elif item.is_dir():
                    if dest_path.exists():
                        shutil.rmtree(dest_path)
                    shutil.copytree(item, dest_path)

            return True
        except Exception as e:
            print(f"WARNING: Failed to restore artifacts for {component}: {e}")
            return False

    def should_rebuild_extension(self, extension_name: str) -> bool:
        """Check if an extension should be rebuilt based on cache status.

        Args:
            extension_name: Name of the extension (e.g., 'ddtrace.internal._rand')

        Returns:
            True if extension should be rebuilt, False if cache is valid
        """
        component = ComponentConfig.get_extension_component(extension_name)
        if not component:
            # Unknown extension, always rebuild to be safe
            return True

        return not self.is_cache_valid(component)

    def get_extension_component(self, extension_name: str) -> Optional[str]:
        """Get component name for an extension."""
        return ComponentConfig.get_extension_component(extension_name)

    def clean_cache(self, components: Optional[List[str]] = None, all_python_versions: bool = False) -> None:
        """Clean cache for specified components or all components.

        Args:
            components: List of component names to clean, or None for all
            all_python_versions: If True, clean caches for all Python versions
        """
        if components is None:
            components = list(self.component_cache_dirs.keys())

        if all_python_versions:
            # Clean all Python version directories
            for py_dir in self.cache_dir.glob("py*"):
                if py_dir.is_dir():
                    for component in components:
                        component_dir = py_dir / component
                        if component_dir.exists():
                            shutil.rmtree(component_dir)
                            print(f"Cleaned cache for component: {component} (Python {py_dir.name})")
        else:
            # Clean only current Python version
            for component in components:
                cache_dir = self.component_cache_dirs.get(component)
                if cache_dir and cache_dir.exists():
                    shutil.rmtree(cache_dir)
                    print(f"Cleaned cache for component: {component} (Python {self.python_version})")

    def get_cache_status(self) -> Dict[str, Dict]:
        """Get status of all component caches."""
        status = {}
        current_hashes = self.get_component_hashes()

        for component in self.component_cache_dirs:
            cache_info = self.load_cache_info(component)
            is_valid = self.is_cache_valid(component)

            status[component] = {
                "valid": is_valid,
                "current_hash": current_hashes.get(component, "unknown"),
                "cached_hash": cache_info.get("hash") if cache_info else None,
                "timestamp": cache_info.get("timestamp") if cache_info else None,
                "has_artifacts": self.get_cached_artifacts_path(component).exists(),
                "python_version": self.python_version,
                "cached_python_version": cache_info.get("additional_info", {}).get("python_version")
                if cache_info
                else None,
            }

        return status

    def print_cache_status(self) -> None:
        """Print human-readable cache status."""
        status = self.get_cache_status()

        print("Build Cache Status:")
        print("=" * 50)
        print(f"Current Python Version: {self.python_version}")

        for component, info in status.items():
            print(f"\n{component}:")
            print(f"  Valid: {'Yes' if info['valid'] else 'No'}")
            print(f"  Current Hash: {info['current_hash'][:8]}...")
            if info["cached_hash"]:
                print(f"  Cached Hash:  {info['cached_hash'][:8]}...")
                if info["timestamp"]:
                    cached_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(info["timestamp"]))
                    print(f"  Cached Time:  {cached_time}")
            else:
                print("  Cached Hash:  None")
            print(f"  Has Artifacts: {'Yes' if info['has_artifacts'] else 'No'}")

            # Show Python version compatibility
            if info["cached_python_version"]:
                if info["cached_python_version"] == info["python_version"]:
                    print(f"  Python Version: {info['cached_python_version']} ✓")
                else:
                    print(f"  Python Version: {info['cached_python_version']} ✗ (current: {info['python_version']})")
            else:
                print("  Python Version: Unknown")


def main():
    """Command-line interface for build cache management."""
    import argparse

    parser = argparse.ArgumentParser(description="Build cache management for ddtrace")
    parser.add_argument("--base-dir", type=Path, help="Base directory for the project")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Status command
    subparsers.add_parser("status", help="Show cache status")

    # Clean command
    clean_parser = subparsers.add_parser("clean", help="Clean cache")
    clean_parser.add_argument("components", nargs="*", help="Components to clean")
    clean_parser.add_argument("--all-python-versions", action="store_true", help="Clean caches for all Python versions")

    # Check command
    check_parser = subparsers.add_parser("check", help="Check if extension needs rebuild")
    check_parser.add_argument("extension", help="Extension name to check")

    args = parser.parse_args()

    cache = BuildCache(args.base_dir)

    if args.command == "status":
        cache.print_cache_status()
    elif args.command == "clean":
        cache.clean_cache(args.components if args.components else None, all_python_versions=args.all_python_versions)
    elif args.command == "check":
        should_rebuild = cache.should_rebuild_extension(args.extension)
        print(f"Extension {args.extension} should be rebuilt: {should_rebuild}")
        exit(0 if not should_rebuild else 1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
