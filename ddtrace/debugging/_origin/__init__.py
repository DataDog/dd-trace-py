from pathlib import Path
import typing as t

from ddtrace.internal.settings.code_origin import config as co_config


# Parse file path rewrite rules once at module load time.
# Format: "old_prefix=new_prefix" pipe-delimited for multiple rules.
# Uses '=' as the delimiter to avoid conflicts with Windows drive letters (C:\).
_rewrite_rules: list[tuple[str, str]] = []
_raw_rewrite = co_config.file_path_rewrite
if _raw_rewrite:
    _rewrite_rules = [tuple(r.split("=", 1)) for r in _raw_rewrite.split("|") if "=" in r]  # type: ignore[misc]


def apply_path_rewrite(filename: str) -> str:
    """Apply the first matching file path rewrite rule to a resolved filename.

    Used by code origin instrumentation to map container-absolute paths to
    repo-relative paths for Datadog Source Code Integration.
    """
    for old, new in _rewrite_rules:
        if filename.startswith(old):
            return new + filename[len(old):]
    return filename


def resolve_code_origin_filename(code_filename: str) -> str:
    """Resolve a code object filename and apply any configured path rewrites."""
    return apply_path_rewrite(str(Path(code_filename).resolve()))
