"""
Shared utilities for coverage collection (both test coverage and runtime coverage).

This module provides common functionality for coverage path resolution and initialization.
"""

import os
from pathlib import Path

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def get_coverage_root_dir() -> Path:
    """
    Determine the root directory for coverage collection.
    
    Tries to use the git repository root first (for consistency with CI Visibility),
    falling back to the current working directory if not in a git repository.
    
    Returns:
        Path object representing the coverage root directory
    """
    try:
        from ddtrace.ext.git import extract_workspace_path
        
        workspace_path = Path(extract_workspace_path())
        log.debug("Using git workspace path as coverage root: %s", workspace_path)
        return workspace_path
    except (ValueError, Exception) as e:
        # Not in a git repository or git command failed
        # Fall back to current working directory
        fallback_path = Path(os.getcwd())
        log.debug("Git workspace not available (%s), using cwd as coverage root: %s", e, fallback_path)
        return fallback_path

