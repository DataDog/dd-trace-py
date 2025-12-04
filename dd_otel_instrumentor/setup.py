#!/usr/bin/env python3
"""
Custom setup.py that syncs ddtrace files before building.

This ensures the ddtrace subset is copied only during build time,
not kept permanently in the source directory.
"""

import hashlib
import shutil
import sys
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.develop import develop
from setuptools.command.egg_info import egg_info


# Root directories
SCRIPT_DIR = Path(__file__).parent
DDTRACE_ROOT = SCRIPT_DIR.parent / "ddtrace"
TARGET_ROOT = SCRIPT_DIR / "ddtrace"


# Files to sync from the main ddtrace package
FILES_TO_SYNC = [
    # Root files
    "__init__.py",
    "constants.py",
    "version.py",
    # _trace module (minimal subset for OTel compat)
    "_trace/__init__.py",
    "_trace/_span_link.py",
    "_trace/context.py",
    "_trace/span.py",
    # contrib/compat - OTel compatibility layer
    "contrib/__init__.py",
    "contrib/compat/__init__.py",
    "contrib/compat/core.py",
    "contrib/compat/otel_patcher.py",
    "contrib/compat/otel_trace_handlers.py",
    # contrib/internal - shared integration utilities
    "contrib/internal/__init__.py",
    "contrib/internal/trace_utils.py",
    "contrib/internal/trace_utils_base.py",
    # Integrations
    "contrib/internal/httpx/__init__.py",
    "contrib/internal/httpx/patch.py",
    # ext module
    "ext/__init__.py",
    "ext/http.py",
    "ext/net.py",
    # internal module (minimal subset)
    "internal/__init__.py",
    "internal/compat.py",
    "internal/constants.py",
    "internal/logger.py",
    # internal/core
    "internal/core/__init__.py",
    "internal/core/event_hub.py",
    # internal/schema
    "internal/schema/__init__.py",
    "internal/schema/span_attribute_schema.py",
    # internal/settings
    "internal/settings/__init__.py",
    "internal/settings/_config.py",
    "internal/settings/_core.py",
    "internal/settings/integration.py",
    # internal/utils
    "internal/utils/__init__.py",
    "internal/utils/formats.py",
    "internal/utils/version.py",
    "internal/utils/wrappers.py",
    # propagation
    "propagation/__init__.py",
    "propagation/_utils.py",
    "propagation/http.py",
]


def sync_ddtrace_files():
    """Sync required files from the main ddtrace package."""
    if not DDTRACE_ROOT.exists():
        print(f"WARNING: ddtrace source not found at {DDTRACE_ROOT}")
        print("Skipping sync - assuming files are already in place (e.g., from sdist)")
        return

    print(f"Syncing ddtrace files from {DDTRACE_ROOT}")

    for rel_path in FILES_TO_SYNC:
        source = DDTRACE_ROOT / rel_path
        target = TARGET_ROOT / rel_path

        if not source.exists():
            print(f"  WARNING: Source file not found: {source}")
            continue

        # Create target directory if needed
        target.parent.mkdir(parents=True, exist_ok=True)

        # Copy file
        shutil.copy2(source, target)

    print(f"Synced {len(FILES_TO_SYNC)} files to {TARGET_ROOT}")


class SyncAndBuild(build_py):
    """Custom build_py that syncs ddtrace files first."""

    def run(self):
        sync_ddtrace_files()
        super().run()


class SyncAndDevelop(develop):
    """Custom develop that syncs ddtrace files first."""

    def run(self):
        sync_ddtrace_files()
        super().run()


class SyncAndEggInfo(egg_info):
    """Custom egg_info that syncs ddtrace files first."""

    def run(self):
        sync_ddtrace_files()
        super().run()


if __name__ == "__main__":
    setup(
        cmdclass={
            "build_py": SyncAndBuild,
            "develop": SyncAndDevelop,
            "egg_info": SyncAndEggInfo,
        }
    )

