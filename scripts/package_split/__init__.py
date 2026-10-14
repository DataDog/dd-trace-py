"""Package-split validation harness.

Versioned, self-contained gate implementations backing the thin
``scripts/package-split`` wrapper. One copy of this package lives in each
participating repository; the wrapper is the only public interface.

See docs/package-split/plan.md (Deliverable 2) for the gate contract and
scripts/package_split/README.md for usage details.
"""

__version__ = "0.1.0"

# Versioned schemas understood by this harness. Bump when a manifest field is
# added, removed, or changes meaning; loaders reject unknown versions so a
# newer manifest is never silently misread by an older harness.
MANIFEST_SCHEMA_VERSION = 1
FREEZE_SCHEMA_VERSION = 1
MATRIX_SCHEMA_VERSION = 1
