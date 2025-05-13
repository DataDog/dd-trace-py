# Datadog Python APM Integration Registry

This directory contains the canonical registry of integrations supported by `dd-trace-py`.

## Purpose

The [`registry.yaml`](./registry.yaml) file serves as a centralized, machine-readable source of truth for metadata about each integration within the `ddtrace/contrib/internal/` directory. This metadata includes essential information about dependencies and version compatibility, used by internal tooling and potentially for documentation generation.

## Format

The registry is stored as a single YAML file: [`registry.yaml`](./registry.yaml). It consists of a root object with a single key, `integrations`, which holds a list of integration definition objects. Each integration object represents one directory found under `ddtrace/contrib/internal/`.

## Schema and Fields

Each integration entry in the `integrations` list adheres to the schema defined in [`_registry_schema.json`](./_registry_schema.json). The key fields are:

**Required Fields:**

* **`integration_name`** (String):
  * The canonical, lowercase, snake_case name of the integration
  * Must match the corresponding directory name within `ddtrace/contrib/internal/`
  * Example: `flask`, `redis`, `asyncio`

* **`is_external_package`** (Boolean):
  * `true`: If the integration instruments a third-party library typically installed via pip (e.g., `flask`, `requests`, `psycopg`)
  * `false`: If the integration instruments a Python standard library module (`asyncio`, `logging`) or internal integration (`dbapi`).

**Optional Fields:**

* **`is_tested`** (Boolean):
  * Indicated if the integration has tests
  * `false` if the integration is untested

* **`dependency_names`** (List of Strings):
  * Present only if `is_external_package` is `true`
  * Lists the primary PyPI package name(s) associated with the integration
  * For integrations patching multiple underlying libraries (like `elasticsearch`), this may list several names
  * Example: `["flask"]`, `["redis"]`, `["elasticsearch", "elasticsearch1", "opensearchpy"]`

* **`tested_versions_by_dependency`** (Object):
  * Present only if `is_external_package` is `true` and `is_tested` is not `false`
  * Maps dependency names to their tested version ranges
  * Each version range includes:
    * `min`: Minimum tested version
    * `max`: Maximum tested version
  * Example:
    ```yaml
    tested_versions_by_dependency:
      flask:
        min: "2.0.0"
        max: "3.0.0"
    ```

## Updating the Registry

The registry is automatically updated through two main mechanisms:

1. **Test Suite Execution**:
   * Running a riot test suite for an integration automatically updates its version information in the registry (if updates are deemed necessary)
   * This happens through the [`IntegrationRegistryManager`](../../../tests/contrib/integration_registry/registry_update_helpers/integration_registry_manager.py) which tracks patched dependencies and their tested versions during test execution

2. **Manual Update Script**:
   * To update all integration information at once, run from the repository root:
     ```bash
     python scripts/integration_registry/update_and_format_registry.py
     ```
   * This script:
     * Runs [`scripts/freshvenvs.py generate`](../../../scripts/freshvenvs.py) to update underlying version data
     * Runs [`scripts/generate_table.py`](../../../scripts/generate_table.py) to create the supported versions table
     * Runs [`scripts/integration_registry/_update_integration_registry_versions.py`](../../../scripts/integration_registry/_update_integration_registry_versions.py) to update the registry
     * Formats the registry YAML for consistency

  **NOTE: Manual script update does not guarantee that newly added patched dependencies (such as for a new integration) will be added to the registry.yaml.**
  
  ***Registry Update Example (Incorrect Workflow)***:
  - Add support for new `integration_a`, including patch files and tests
  - Manually run `python scripts/integration_registry/update_and_format_registry.py` WITHOUT running riot test suite for `integration_a`.
  - **OUTCOME**: Existing integration and dependencies are updated, but the new `integration_a` and its dependencies will not be added to `registry.yaml`.
  
  ***Registry Update Example (Correct Workflow)***:
  - Add support for new `integration_a`, including patch files and tests
  -  Do a full riot test run of the newly added `integration_a` test suite. This is needed because we cannot reliably map dependency name to the integration name if they are not equal (such as integration == `rediscluster` and dependency name == `redis-py-cluster`). Instead, during the riot test suite run, we rely on collecting the patched module, along with the integration name via the [`IntegrationRegistryManager`](../../../tests/contrib/integration_registry/registry_update_helpers/integration_registry_manager.py). With the patched module, we can map the patched module to the dependency name using `importlib.metadata`, and in the `rediscluster` case, we get: `redis-py-cluster` as a dependency. Then we can update the registry since we now know the dependency name of interest, and the related integration name. 
  - **OUTCOME**: After running our new test suite for `integration_a`, the new integration along with its dependencies are automatically added to `registry.yaml`. Existing integrations amd dependencies are also updated.

  **FURTHER-NOTE: [`IntegrationRegistryManager`](../../../tests/contrib/integration_registry/registry_update_helpers/integration_registry_manager.py#158) relies on the use of `_datadog_patch` to collect patched modules. Please ensure this attribute is set on the patched module within the integration's patch function. Here is an example for the `aiohttp` integration [`aiohttp patch.py`](../../../ddtrace/contrib/internal/aiohttp/patch.py#139)**

## Adding New Integrations

When adding a new integration:

1. Create the integration directory and implementation in `ddtrace/contrib/internal/`
    - Ensure the patched module has `_datadog_patch=True`. The integration registry test code uses this attribute to determine which dependencies are patched, and that within the `patch()` function, the integration uses `getattr(module, '_datadog_patch') is True`.
2. Add tests and a corresponding riot test suite in [`riotfile.py`](../../../riotfile.py)
3. Run the test suite - this will automatically:
   * Add the integration to the registry
   * Record its dependency information
   * Track tested version ranges

No manual registry updates are needed - the `IntegrationRegistryManager` and update scripts handle everything automatically.

## Registry Tests

The registry has a test suite in [`tests/contrib/integration_registry/`](../../../tests/contrib/integration_registry/):

* [`test_registry_schema.py`](../../../tests/contrib/integration_registry/test_registry_schema.py):
  * Validates that the registry YAML content strictly conforms to the JSON schema definition
  * Ensures all required fields are present and correctly formatted
  * Verifies that every directory in `ddtrace/contrib/internal` has a corresponding entry in the registry
  * Checks for any orphaned registry entries that don't have matching directories

* [`test_external_dependencies.py`](../../../tests/contrib/integration_registry/test_external_dependencies.py):
  * Validates external package requirements and version information:
    * Ensures external integrations have required `dependency_names` and `tested_versions_by_dependency` fields
    * Verifies version strings follow semantic versioning format
    * Checks that version maps match declared dependencies
  * Verifies all declared dependencies exist on PyPI:
    * Uses `pip index versions` to check each package
    * Validates package names are available and accessible
    * Reports detailed errors for missing or invalid packages
  * Ensures non-external integrations don't have dependency-related fields

* [`test_riotfile.py`](../../../tests/contrib/integration_registry/test_riotfile.py):
  * Verifies every integration has corresponding test environments in `riotfile.py`:
    * Checks that each integration directory has a matching riot environment
    * Excludes explicitly untested integrations
    * Reports missing test environment definitions
  * Validates test paths in riot environments:
    * Ensures test paths under `tests/contrib` correspond to actual integrations
    * Handles special cases for utility test environments
    * Verifies proper organization of integration-specific tests

## Troubleshooting

### Running the Integration Registry Updater Locally

If you need to debug or manually run the integration registry update process, the necessary code is located within the `integration_update_orchestrator.py` script. Follow these steps:

1.  Navigate to the [code section containing the local run logic](tests/contrib/integration_registry/registry_update_helpers/integration_update_orchestrator.py#L175-L183).
2.  Uncomment the Python code block as indicated and comment out the the lines previous that run the updater in a subprocess.
3.  Ensure the required dependencies (`filelock`, `pyyaml`) are installed in the riot environment you are running. You need to temporarily add them to the relevant environment definition in `riotfile.py`. 
4.  Execute the test suite, and place a breakpoint in your choice of code for the `IntegrationRegistryUpdater`.

## Related Files

* [`registry.yaml`](./registry.yaml) - The main registry file
* [`_registry_schema.json`](./_registry_schema.json) - JSON Schema definition
* [`mappings.py`](./mappings.py) - Mappings of integration name to dependency name, and the other way around as well. Also includes special cases for dependency name mappings to integration.
* [`IntegrationRegistryManager`](../../../tests/contrib/integration_registry/registry_update_helpers/integration_registry_manager.py) 
  - Patches `getattr()` and listens for modules that have `_datadog_patch` set on them.
  - Collects all modules that had a patch set and saves them for later processing.
  - Produces a dict of the form: `{ integration_name: { dependency_names: { "version": dep_version, "top_level_module": patched_top_level_module } } }`
  - Cleans up all fixtures after the test session
* [`IntegrationRegistryUpdater`](../../../tests/contrib/integration_registry/registry_update_helpers/integration_registry_updater.py)
  - Reads collected dictionary of integrations and patched dependencies during test run from `IntegrationRegistryManager`
  - Reads current `registry.yaml` file, and determines if the file should be updated by looking for the presence of the integration, dependency, or if
  the tested version is outside the currently listed tested range.
  - Updates `registry.yaml` if necessary
* [`IntegrationUpdateOrchestrator`](../../../tests/contrib/integration_registry/registry_update_helpers/integration_update_orchestrator.py)
  - Builds a virtual environment to allow the integration registry updater process to run in another thread. Installs `riot` and `pyyaml` dependencies necessary for update.
  - Runs `IntegrationRegistryUpdater`
  - Runs [`update_and_format_registry.py`](../../../scripts/integration_registry/update_and_format_registry.py) script if updates are deemed necessary.
* Update Scripts:
  * [`update_and_format_registry.py`](../../../scripts/integration_registry/update_and_format_registry.py) - Main update script, runs all the below scripts
  * [`_update_integration_registry_versions.py`](../../../scripts/integration_registry/_update_integration_registry_versions.py) - Updates version information within the registry
  * [`freshvenvs.py`](../../../scripts/freshvenvs.py) - Generates tested integration version data from test environments
  * [`generate_table.py`](../../../scripts/generate_table.py) - Creates supported versions table
