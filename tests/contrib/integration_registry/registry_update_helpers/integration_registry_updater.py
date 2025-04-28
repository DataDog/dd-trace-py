import json
import pathlib
import sys
from typing import Tuple
from typing import Union

from filelock import FileLock
import yaml

from tests.contrib.integration_registry.registry_update_helpers.integration import Integration


class IntegrationRegistryUpdater:
    """
    Handles loading, merging, and writing integration registry data using file locking. Checks if the registry needs
    to be updated before merging/writing.
    """

    def __init__(self):
        self.project_root = self._find_project_root()
        if not self.project_root:
            raise RuntimeError("Could not determine project root directory.")

        self.registry_yaml_path = self.project_root / "ddtrace" / "contrib" / "integration_registry" / "registry.yaml"
        self.registry_lock_path = (
            self.project_root / "ddtrace" / "contrib" / "integration_registry" / "registry.yaml.lock"
        )
        self.lock_timeout_seconds = 15
        self.lock = FileLock(self.registry_lock_path, timeout=self.lock_timeout_seconds)
        self.raw_registry_data: dict[str, dict] = {}
        self.integrations: dict[str, Integration] = {}

    def _find_project_root(self) -> Union[pathlib.Path, None]:
        """Finds the project root by searching upwards for marker files."""
        current_dir = pathlib.Path(__file__).parent
        for _ in range(5):
            if (current_dir / "pyproject.toml").exists() or (current_dir / ".git").exists():
                return current_dir
            if current_dir.parent == current_dir:
                break
            current_dir = current_dir.parent
        return None

    def _load_integrations(self):
        """Loads the integrations from the registry data into a class instance."""
        for integration in self.raw_registry_data.get("integrations", []):
            if not isinstance(integration, dict):
                continue
            self.integrations[integration["integration_name"]] = Integration(**integration)

    def load_registry_data(self):
        """Safely loads the main registry YAML using a file lock."""
        try:
            self.lock.acquire(timeout=self.lock_timeout_seconds)
            if not self.registry_yaml_path.exists():
                self.raw_registry_data = {}
                return
            with open(self.registry_yaml_path, "r", encoding="utf-8") as f:
                self.raw_registry_data = yaml.safe_load(f)
                if self.raw_registry_data:
                    self._load_integrations()
        except Exception:
            if self.lock.is_locked:
                self.lock.release()

    def load_input_data(self, input_file_path_str: str) -> dict:
        """Loads the JSON data from the specified input file."""
        input_file_path = pathlib.Path(input_file_path_str)
        try:
            with open(input_file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _needs_update(self, input_data: dict) -> bool:
        """Checks if input_data contains info not present in registry_data."""
        for integration_name, updates in input_data.items():
            # if the integration is not tested, we don't need to update
            new_deps = updates
            if not new_deps:
                continue

            # if the integration is not in the registry, we need to update
            if integration_name not in self.integrations:
                return True

            # check if the integration should be updated
            if not self.integrations[integration_name].should_update(updates):
                return False
        return True

    def merge_data(self, new_dependency_versions: dict) -> Tuple[int, int]:
        """Merges dependency info from new_dependency_versions into registry_data. Assumes check already done."""
        # loop through the new integration data and add the updates to the registry
        added_integrations = 0
        updated_integrations = 0
        for integration_name, updates in new_dependency_versions.items():
            if not updates:
                continue

            # if the integration is not in the registry, add it
            if integration_name not in self.integrations:
                self.integrations[integration_name] = Integration(
                    integration_name=integration_name,
                    is_external_package=True,
                    dependency_name=sorted(list(set(updates.keys()))),
                )
                self.integrations[integration_name].update(updates, update_versions=True)
                added_integrations += 1
                continue
            else:
                # update the existing integration
                changed = self.integrations[integration_name].update(updates, update_versions=True)
                if changed:
                    updated_integrations += 1

        return added_integrations, updated_integrations

    def write_registry_data(self) -> bool:
        """Safely writes the updated data back to registry YAML using a file lock."""
        # Convert Integration objects to dictionaries and sort by integration_name
        integrations_list = sorted(
            [integration.to_dict() for integration in self.integrations.values()],
            key=lambda x: x["integration_name"],
        )
        data_to_write = {"integrations": integrations_list}
        try:
            with open(self.registry_yaml_path, "w", encoding="utf-8") as f:
                yaml.dump(
                    data_to_write,
                    f,
                    default_flow_style=False,
                    sort_keys=False,
                    indent=2,
                    width=100,
                )
            return True
        except Exception as e:
            print(f"\nIntegrationRegistryUpdater: Failed to write updated registry data: {e}", file=sys.stderr)
            return False
        finally:
            self._delete_lock_file()
            if self.lock.is_locked:
                self.lock.release()

    def _delete_lock_file(self):
        """Deletes the lock file if it exists."""
        try:
            if self.registry_lock_path.exists():
                self.registry_lock_path.unlink()
        except OSError as e:
            print(f"IntegrationRegistryUpdater: Failed to delete lock file: {e}", file=sys.stderr)

    def run(self, input_file_path_str: str) -> bool:
        """
        Loads data, checks if update needed, merges/writes if necessary.
        Returns True ONLY if changes were successfully written, False otherwise.
        """
        input_data = self.load_input_data(input_file_path_str)
        if not input_data:
            return False

        changes_made = False
        try:
            self.load_registry_data()

            # if the registry data is up to date, we can skip the merge and write steps, and release the lock
            if not self._needs_update(input_data):
                if self.lock.is_locked:
                    self.lock.release()
                return False

            # merge the input data into the registry data
            self.merge_data(input_data)

            changes_made = True
            if not self.write_registry_data():
                print("\nIntegrationRegistryUpdater: Failed to write updated registry data.", file=sys.stderr)
                return False

            return changes_made

        except Exception as e:
            print(f"\nIntegrationRegistryUpdater: Error during run: {e}", file=sys.stderr)
            # Ensure lock is released on any exception if still held (e.g., error between load and write)
            if self.lock.is_locked:
                self.lock.release()
            return False
        finally:
            # Ensure lock is always released and the lock file is deleted
            if self.lock.is_locked:
                self.lock.release()
            self._delete_lock_file()
