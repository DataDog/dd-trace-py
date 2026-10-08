import json
import os
import pathlib
import tempfile
from typing import Union

from filelock import FileLock
import yaml

from .integration import Integration


class IntegrationRegistryUpdater:
    """
    Handles loading, merging, and writing integration registry data using file locking. Checks if the registry needs
    to be updated before merging/writing.
    """

    def __init__(self):
        self.project_root = self._find_project_root()
        if not self.project_root:
            raise RuntimeError("Could not determine project root directory.")

        self.registry_yaml_path = self.project_root / "scripts" / "integration_registry" / "registry.yaml"
        self.registry_lock_path = self.project_root / "scripts" / "integration_registry" / "registry.yaml.lock"
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
            self.integrations[integration["integration_name"]] = Integration(**integration)

    def load_registry_data(self):
        """Safely loads the main registry YAML using a file lock."""
        try:
            self.lock.acquire(timeout=self.lock_timeout_seconds)
            with open(self.registry_yaml_path, encoding="utf-8") as f:
                self.raw_registry_data = yaml.safe_load(f)
            if (
                not isinstance(self.raw_registry_data, dict)
                or not isinstance(self.raw_registry_data.get("integrations"), list)
                or not self.raw_registry_data["integrations"]
            ):
                raise ValueError("Integration registry must contain a non-empty integrations list.")
            self._load_integrations()
        except Exception:
            if self.lock.is_locked:
                self.lock.release()
            raise

    def load_input_data(self, input_file_path_str: str) -> dict:
        """Loads the JSON data from the specified input file."""
        input_file_path = pathlib.Path(input_file_path_str)
        try:
            with open(input_file_path, encoding="utf-8") as f:
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

    def merge_data(self, new_dependency_versions: dict) -> tuple[int, int]:
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
                    dependency_names=sorted(list(set(updates.keys()))),
                )
                self.integrations[integration_name].update(updates, update_versions=True)
                added_integrations += 1
                continue
            else:
                test_suite = self._get_test_suite_name()

                # update the existing integration
                changed = self.integrations[integration_name].update(
                    updates, update_versions=True, test_suite=test_suite
                )
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
        if not self.lock.is_locked:
            raise RuntimeError("Registry writes require the lock acquired when loading the registry.")
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.registry_yaml_path.parent, delete=False
            ) as f:
                temporary_path = pathlib.Path(f.name)
                yaml.dump(
                    data_to_write,
                    f,
                    default_flow_style=False,
                    sort_keys=False,
                    indent=2,
                    width=100,
                )
            os.chmod(temporary_path, self.registry_yaml_path.stat().st_mode)
            temporary_path.replace(self.registry_yaml_path)
            return True
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            if self.lock.is_locked:
                self.lock.release()

    def _get_test_suite_name(self):
        """Return the integration name when this runs inside a test suite."""
        if test_suite := os.environ.get("TEST_SUITE"):
            return test_suite.split("::")[-1].split(":")[0]
        return None

    def run(self, input_file_path_str: str) -> bool:
        """
        Loads data, checks if update needed, merges/writes if necessary.
        Returns True ONLY if changes were successfully written, False otherwise.
        """
        input_data = self.load_input_data(input_file_path_str)
        if not input_data:
            return False

        try:
            self.load_registry_data()
            if not self._needs_update(input_data):
                return False

            added_integrations, updated_integrations = self.merge_data(input_data)
            if added_integrations == 0 and updated_integrations == 0:
                return False

            return self.write_registry_data()
        finally:
            # AIDEV-NOTE: Keep the lock file in place so every process locks the same inode.
            if self.lock.is_locked:
                self.lock.release()
