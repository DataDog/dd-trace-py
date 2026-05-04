import builtins
from collections.abc import Mapping
import importlib.metadata
import json
from pathlib import Path
import sys
import traceback


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEPENDENCY_NAMES_PATH = PROJECT_ROOT / "scripts" / "supported_version" / "dependency_names.json"
CI_TESTED_VERSIONS_PATH = PROJECT_ROOT / "ci_tested_versions.json"


class DependencyNameUpdater:
    """Collect patched integration modules and add missing dependency names."""

    def __init__(self, dependency_names_path: Path = DEPENDENCY_NAMES_PATH):
        self.dependency_names_path = dependency_names_path
        self.original_getattr = None
        self.patched_objects = {}
        self.processed_objects = set()
        self.packages_distributions = None

    def patch_getattr(self) -> None:
        """Patch getattr to record modules checked for Datadog patch markers."""
        if self.original_getattr is not None:
            return

        self.original_getattr = builtins.getattr
        original_getattr = self.original_getattr

        def _wrapped_getattr(obj, name, *default):
            if name in ("_datadog_patch", "__datadog_patch") and obj not in self.processed_objects:
                if obj not in self.patched_objects:
                    tb = traceback.extract_stack()[:-1]
                    self.patched_objects[obj] = "".join(traceback.format_list(tb))

            if default:
                return original_getattr(obj, name, default[0])
            return original_getattr(obj, name)

        builtins.getattr = _wrapped_getattr

    def cleanup_patch(self) -> None:
        """Restore the original getattr implementation."""
        if self.original_getattr is not None:
            builtins.getattr = self.original_getattr
            self.original_getattr = None

    def cleanup_post_session(self) -> None:
        """Clear all session state."""
        self.cleanup_patch()
        self.patched_objects.clear()
        self.processed_objects.clear()
        self.packages_distributions = None

    def process_patched_objects(self) -> dict[str, set[str]]:
        """Return discovered dependency names keyed by integration name."""
        discovered: dict[str, set[str]] = {}

        for obj, integration_name, distribution_name in self._iter_patched_dependencies():
            discovered.setdefault(integration_name, set()).add(distribution_name)
            self.processed_objects.add(obj)

        return discovered

    def collect_session_tested_versions(self) -> set[tuple[str, str, str, str]]:
        """Return dependency versions observed from patched modules in this test session."""
        return {
            (
                integration_name,
                distribution_name,
                importlib.metadata.version(distribution_name),
                f"{sys.version_info.major}.{sys.version_info.minor}",
            )
            for _, integration_name, distribution_name in self._iter_patched_dependencies()
        }

    def get_missing_tested_versions(
        self, observed_versions: set[tuple[str, str, str, str]]
    ) -> list[dict[str, str]]:
        """Return tested versions from this session that are missing from ci_tested_versions.json."""
        if not observed_versions:
            return []

        current_versions = self._get_ci_tested_versions()
        return [
            {
                "integration_name": integration_name,
                "dependency_name": dependency_name,
                "version": version,
                "python_version": python_version,
            }
            for integration_name, dependency_name, version, python_version in sorted(observed_versions - current_versions)
        ]

    def update_dependency_names(self, discovered_dependencies: dict[str, set[str]]) -> bool:
        """Append missing dependency names and write the JSON file if it changed."""
        if not discovered_dependencies:
            return False

        data = json.loads(self.dependency_names_path.read_text())
        integrations = {
            integration["integration_name"]: set(integration.get("dependency_names", []))
            for integration in data.get("integrations", [])
        }

        changed = False
        for integration_name, dependency_names in discovered_dependencies.items():
            existing_dependency_names = integrations.setdefault(integration_name, set())
            missing_dependency_names = dependency_names - existing_dependency_names
            if missing_dependency_names:
                existing_dependency_names.update(missing_dependency_names)
                changed = True

        if not changed:
            return False

        data["integrations"] = [
            {
                "integration_name": integration_name,
                "dependency_names": sorted(dependency_names),
            }
            for integration_name, dependency_names in sorted(integrations.items())
        ]
        self.dependency_names_path.write_text(json.dumps(data, indent=2) + "\n")
        self._generate_tested_versions()
        return True

    def update_from_patched_objects(self) -> bool:
        """Process collected patch markers and add any missing dependency names."""
        return self.update_dependency_names(self.process_patched_objects())

    def _generate_tested_versions(self) -> None:
        """Regenerate tested versions after dependency name mappings change."""
        from scripts.supported_version.generate_tested_versions import main as generate_tested_versions

        generate_tested_versions()

    def _get_packages_distributions(self) -> Mapping[str, list[str]]:
        if self.packages_distributions is None:
            self.packages_distributions = importlib.metadata.packages_distributions()
        return self.packages_distributions

    def _get_ci_tested_versions(self) -> set[tuple[str, str, str, str]]:
        if not CI_TESTED_VERSIONS_PATH.exists():
            return set()

        data = json.loads(CI_TESTED_VERSIONS_PATH.read_text())
        return {
            (
                entry["integration_name"],
                entry["dependency_name"],
                tested_version["version"],
                tested_version["python_version"],
            )
            for entry in data
            for tested_version in entry.get("tested_versions", [])
        }

    def _iter_patched_dependencies(self):
        for obj, tb_string in list(self.patched_objects.items()):
            if obj in self.processed_objects or not self._is_valid_patch_call(tb_string):
                continue

            full_module_name = self._get_full_module_from_object(obj)
            integration_name = self._get_integration_name_from_traceback(tb_string)
            if not full_module_name or not integration_name:
                continue

            top_level_module = full_module_name.split(".", 1)[0]
            for distribution_name in self._get_packages_distributions().get(top_level_module, []):
                if self._distribution_contains_module(distribution_name, full_module_name):
                    yield obj, integration_name, distribution_name

    def _is_valid_patch_call(self, tb_string: str) -> bool:
        return any(
            "ddtrace/contrib/internal" in line and "/patch.py" in line for line in reversed(tb_string.splitlines())
        )

    def _get_integration_name_from_traceback(self, tb_string: str) -> str | None:
        for line in reversed(tb_string.splitlines()):
            if "ddtrace/contrib/internal/" not in line:
                continue

            parts = line.split("ddtrace/contrib/internal/", 1)[1].split("/")
            if parts and parts[0]:
                return parts[0]
        return None

    def _get_full_module_from_object(self, obj) -> str | None:
        for attr in ("__module__", "__name__"):
            try:
                module_name = self.original_getattr(obj, attr) if self.original_getattr else builtins.getattr(obj, attr)
            except Exception:
                continue

            if isinstance(module_name, str) and module_name:
                return module_name
        return None

    def _distribution_contains_module(self, distribution_name: str, full_module_name: str) -> bool:
        try:
            dist_files = importlib.metadata.files(distribution_name)
        except Exception:
            return False

        if not dist_files:
            return False

        module_path = full_module_name.replace(".", "/")
        expected_paths = (f"{module_path}.py", f"{module_path}/__init__.py")
        return any(str(file).endswith(expected_paths) for file in dist_files)


dependency_name_updater = DependencyNameUpdater()
