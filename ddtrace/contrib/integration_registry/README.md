# Datadog Python APM Integration Registry

This directory contains the canonical registry of integrations supported by `dd-trace-py`.

## Purpose

The `registry.yaml` file serves as a centralized, machine-readable source of truth for metadata about each integration within the `ddtrace/contrib/internal/` directory. This metadata includes essential information about dependencies and version compatibility, intended primarily for internal tooling and potentially for documentation generation.

## Format

The registry is stored as a single YAML file: `registry.yaml`.

It consists of a root object with a single key, `integrations`, which holds a list of integration definition objects. Each integration object represents one directory found under `ddtrace/contrib/internal/`.

## Schema and Fields

Each integration entry in the `integrations` list adheres to the following structure (defined formally in `_registry_schema.json`):

**Required Fields:**

*   **`integration_name`** (String):
    *   The canonical, lowercase, snake\_case name of the integration.
    *   This MUST match the corresponding directory name within `ddtrace/contrib/internal/`.
    *   Example: `flask`, `redis`, `asyncio`
*   **`is_external_package`** (Boolean):
    *   `true`: If the integration instruments a third-party library typically installed via pip (e.g., `flask`, `requests`, `psycopg`).
    *   `false`: If the integration instruments a Python standard library module (`asyncio`, `logging`), or an internal ddtrace helper (`ddtrace_api`).

**Optional Fields:**

*   **`dependency_name`** (List of Strings):
    *   Present only if `is_external_package` is `true` and corresponding PyPI package name(s) were identified.
    *   Lists the primary PyPI package name(s) associated with the integration. For integrations patching multiple underlying libraries (like `elasticsearch`), this may list several names.
    *   Dependency names should be inputted by the engineer implementing the integration.
    *   Example: `["flask"]`, `["redis"]`, `["elasticsearch", "elasticsearch1", ..., "opensearchpy"]`
*   **`supported_version_min`** (String):
    *   Present only if `is_external_package` is `true` and version information was found in `supported_versions_table.csv`.
    *   The minimum library version officially supported by the tracer, formatted as `MAJOR.MINOR.PATCH`.
    *   Example: `"2.10.6"`
*   **`supported_version_max`** (String):
    *   Present only if `is_external_package` is `true` and version information was found in `supported_versions_table.csv`.
    *   The maximum library version officially supported by the tracer, formatted as `MAJOR.MINOR.PATCH`.
    *   Example: `"5.2.1"`

## Updating the Registry

Unlike previous iterations where the file was fully auto-generated, `registry.yaml` now follows a partially manual update process:

1.  **Manual Updates:** When adding a new integration or changing the core dependency of an existing one:
    *   Manually add or modify the entry for the integration in `ddtrace/contrib/integration_registry/registry.yaml`.
    *   Ensure you accurately set the `integration_name` (matching the directory name), `is_external_package` (true/false), and `dependency_name` (list of PyPI package names, only if `is_external_package` is true).
    *   Leave the version fields (`supported_version_min`, `supported_version_max`) blank or unchanged for now.

2.  **Automatic Version Population:** To update the `supported_version_min` and `supported_version_max` fields based on the latest `supported_versions_table.csv`:
    *   Run the combined update script from the **repository root**: 
        ```bash
        python scripts/integration_registry/update_and_format_registry.py
        ```
    *   This script performs several steps:
        *   Runs `scripts/freshvenvs.py generate` to update underlying version data.
        *   Runs `scripts/generate_table.py` to create `supported_versions_table.csv`.
        *   Runs `scripts/integration_registry/_update_integration_registry_versions.py` to update `registry.yaml` using the new table.
        *   Runs `scripts/integration_registry/_format_integration_registry.py` (called internally by the update script) to format `registry.yaml`.

3.  **Formatting (Optional but Recommended):** If you made manual edits *after* running the update script, or if you want to re-format:
    ```bash
    python scripts/integration_registry/_format_integration_registry.py
    ```

4.  **Review Changes:** Check the diff for `registry.yaml` to ensure the updates (both manual and automated) are correct.

5.  **Commit:** Add the updated `registry.yaml` to your commit.
