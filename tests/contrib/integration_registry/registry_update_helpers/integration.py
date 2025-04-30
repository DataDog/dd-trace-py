class Integration:
    """
    Represents an integration in the registry.
    """

    def __init__(
        self,
        integration_name: str,
        is_external_package: bool = True,
        dependency_names: list = None,
        tested_versions_by_dependency: dict = None,
        is_tested: bool = True,
    ):
        self.integration_name = integration_name
        self.is_external_package = is_external_package
        self.dependency_names = set(dependency_names or [])
        # Initialize with a new dict if None is passed to avoid sharing the mutable default
        self.tested_versions_by_dependency = (
            tested_versions_by_dependency if tested_versions_by_dependency is not None else {}
        )
        self.is_tested = is_tested

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

    def should_update(self, new_dependency_versions: dict) -> bool:
        """Checks if the integration should be updated based on the new dependency versions."""
        for dep, dep_info in new_dependency_versions.items():
            # if the dependency is not in the registry, we need to update
            if dep.lower() not in self.dependency_names:
                return True

            # if the dependency version is not in the registry, we need to update
            dep_version = dep_info.get("version")
            if dep_version == "":
                return False
            min_version = self.tested_versions_by_dependency.get(dep.lower(), None).get("min", None)

            # if the dependency version is not in the registry, we need to update
            if min_version is None:
                return True

            # if the dependency version is less than the min version, we need to update
            if self._semver_compare(dep_version, min_version) == -1:
                return True

            # if the dependency version is greater than the max version, we need to update
            max_version = self.tested_versions_by_dependency.get(dep.lower(), {}).get("max", None)
            if max_version is None:
                return True
            if self._semver_compare(dep_version, max_version) == 1:
                return True
        return False

    def update(self, updates: dict, update_versions: bool = False) -> bool:
        """Updates the integration with the new dependency versions."""
        # skip if the integration is not an external package
        if not self.is_external_package:
            return

        # update the dependency info
        changed = False
        for dep_name in updates.keys():
            dep_name_lower = dep_name.lower()

            if dep_name_lower in self.dependency_names:
                continue

            # if the dependency is not in the registry, add it
            self.dependency_names.add(dep_name_lower)
            changed = True
        if update_versions:
            # update the dependency versions
            prev = self.tested_versions_by_dependency.copy()
            for dep_name in updates.keys():
                dep_name_lower = dep_name.lower()
                self.tested_versions_by_dependency[dep_name_lower] = updates[dep_name]
            changed = prev != self.tested_versions_by_dependency or changed
        return changed

    def to_dict(self) -> dict:
        """Converts the Integration object to a dictionary for YAML serialization."""
        data = {
            "integration_name": self.integration_name,
            "is_external_package": self.is_external_package,
            "is_tested": self.is_tested,
        }
        if self.dependency_names:
            data["dependency_names"] = sorted(list(self.dependency_names))
        if self.tested_versions_by_dependency:
            # Sort tested_versions_by_dependency by key for consistent output
            data["tested_versions_by_dependency"] = dict(sorted(self.tested_versions_by_dependency.items()))
        return data
