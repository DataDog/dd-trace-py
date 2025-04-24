import json
import pathlib
import sys
from typing import Union

from filelock import FileLock
import yaml


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

    def load_registry_data(self) -> dict:
        """Safely loads the main registry YAML using a file lock."""
        try:
            self.lock.acquire(timeout=self.lock_timeout_seconds)
            if not self.registry_yaml_path.exists():
                return {"integrations": []}
            with open(self.registry_yaml_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                return data or {"integrations": []}
        except Exception:
            if self.lock.is_locked:
                self.lock.release()
            return {"integrations": []}

    def load_input_data(self, input_file_path_str: str) -> dict:
        """Loads the JSON data from the specified input file."""
        input_file_path = pathlib.Path(input_file_path_str)
        try:
            with open(input_file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _semver_compare(self, version1: str, version2: str) -> int:
        """
        Compares two semantic version strings (X.Y.Z format).
        Returns:
           1 if version1 > version2
           0 if version1 == version2
          -1 if version1 < version2
        Handles basic X.Y.Z format, raises ValueError for invalid input.
        """
        if version1 == version2:
            return 0

        v1_parts = [int(p) for p in version1.split(".")]
        v2_parts = [int(p) for p in version2.split(".")]

        if v1_parts[0] > v2_parts[0]:
            return 1
        if v1_parts[0] < v2_parts[0]:
            return -1

        if v1_parts[1] > v2_parts[1]:
            return 1
        if v1_parts[1] < v2_parts[1]:
            return -1

        if v1_parts[2] > v2_parts[2]:
            return 1
        if v1_parts[2] < v2_parts[2]:
            return -1

        return 0

    def _needs_update(self, registry_data: dict, input_data: dict) -> bool:
        """Checks if input_data contains info not present in registry_data."""
        integrations_list = registry_data.get("integrations", [])
        registry_map = {
            entry.get("integration_name"): entry
            for entry in integrations_list
            if isinstance(entry, dict) and entry.get("integration_name")
        }

        for integration_name, updates in input_data.items():
            new_deps = updates
            if not new_deps:
                continue

            # if the integration is not in the registry, we need to update
            if integration_name not in registry_map:
                return True

            # if the integration is not an external package, we don't need to update
            entry = registry_map[integration_name]
            if not entry.get("is_external_package"):
                continue

            for dep, dep_info in new_deps.items():
                current_deps_set = set(entry.get("dependency_name", []))

                # if the dependency is not in the registry, we need to update
                if dep.lower() not in current_deps_set:
                    return True

                # if the dependency version is not in the registry, we need to update
                dep_version = dep_info.get("version")
                if dep_version == "":
                    return False
                min_version = entry.get("tested_versions_by_dependency", {}).get(dep.lower(), None).get("min", None)

                # if the dependency version is not in the registry, we need to update
                if min_version is None:
                    return True

                # if the dependency version is less than the min version, we need to update
                if self._semver_compare(dep_version, min_version) == -1:
                    return True

                # if the dependency version is greater than the max version, we need to update
                max_version = entry.get("tested_versions_by_dependency", {}).get(dep.lower(), {}).get("max", None)
                if max_version is None:
                    return True
                if self._semver_compare(dep_version, max_version) == 1:
                    return True
        return False

    def merge_data(self, registry_data: dict, new_dependency_versions: dict) -> bool:
        """Merges dependency info from new_dependency_versions into registry_data. Assumes check already done."""
        integrations_list = registry_data.setdefault("integrations", [])
        registry_map = {
            entry.get("integration_name"): entry
            for entry in integrations_list
            if isinstance(entry, dict) and entry.get("integration_name")
        }
        changed = False

        # loop through the new integration data and add the updates to the registry
        for integration_name, updates in new_dependency_versions.items():
            new_deps_set = set(updates.get("dependency_name", []))
            if not new_deps_set:
                continue

            # if the integration is not in the registry, add it
            if integration_name not in registry_map:
                new_entry = {
                    "integration_name": integration_name,
                    "is_external_package": True,
                    "dependency_name": sorted(list(new_deps_set)),
                }
                integrations_list.append(new_entry)
                changed = True
                continue

            entry = registry_map[integration_name]
            # skip if the integration is not an external package
            if not entry.get("is_external_package"):
                continue

            # if the integration is in the registry, update the dependency info
            current_deps_set = set(entry.get("dependency_name", []))
            for dep_name in new_deps_set:
                dep_name_lower = dep_name.lower()

                if dep_name_lower in current_deps_set:
                    continue

                # if the dependency is not in the registry, add it
                current_deps_set.add(dep_name_lower)
                changed = True
            # update the entry
            entry["dependency_name"] = sorted(list(current_deps_set))

        if changed:
            registry_data["integrations"] = sorted(integrations_list, key=lambda x: x.get("integration_name", ""))
        return changed

    def write_registry_data(self, registry_data: dict) -> bool:
        """Safely writes the updated data back to registry YAML using a file lock."""
        try:
            with open(self.registry_yaml_path, "w", encoding="utf-8") as f:
                yaml.dump(
                    registry_data,
                    f,
                    default_flow_style=False,
                    sort_keys=False,
                    indent=2,
                    width=100,
                )
            return True
        except Exception:
            return False
        finally:
            if self.lock.is_locked:
                self.lock.release()

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
            registry_data = self.load_registry_data()

            # if the registry data is up to date, we can skip the merge and write steps, and release the lock
            if not self._needs_update(registry_data, input_data):
                if self.lock.is_locked:
                    self.lock.release()
                return False

            # merge the input data into the registry data
            self.merge_data(registry_data, input_data)

            changes_made = True
            if not self.write_registry_data(registry_data):
                print("\nIntegrationRegistryUpdater: Failed to write updated registry data.", file=sys.stderr)
                return False

            return True

        except Exception as e:
            print(f"\nIntegrationRegistryUpdater: Error during run: {e}", file=sys.stderr)
            # Ensure lock is released on any exception if still held (e.g., error between load and write)
            if self.lock.is_locked:
                self.lock.release()
            return False
        finally:
            if not changes_made and self.lock.is_locked:
                self.lock.release()
