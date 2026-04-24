import unittest

from scripts.riot_selection import RiotInstanceMetadata
from scripts.riot_selection import RiotSelectionFilters
from scripts.riot_selection import _load_integration_to_dependency_map
from scripts.riot_selection import resolve_suite_name
from scripts.riot_selection import select_instances_for_suite


class RiotSelectionTestCase(unittest.TestCase):
    def test_resolve_suite_name_accepts_short_suffix(self):
        resolved = resolve_suite_name(
            "kombu",
            {
                "contrib::kombu": {},
                "contrib::redis": {},
            },
        )

        self.assertEqual(resolved, "contrib::kombu")

    def test_select_instances_filters_exact_version_and_python(self):
        selection = select_instances_for_suite(
            "contrib::kombu",
            [
                RiotInstanceMetadata(hash="f81bf39", python_version="3.9", packages={"kombu": ">=4.6,<4.7"}),
                RiotInstanceMetadata(hash="c285110", python_version="3.10", packages={"kombu": ">=5.2,<5.3"}),
                RiotInstanceMetadata(hash="1e82f55", python_version="3.10", packages={"kombu": ""}),
            ],
            filters=RiotSelectionFilters(package_version="5.2.1", python_version="3.10"),
            integration_to_dependencies={"kombu": {"kombu"}},
        )

        self.assertEqual(selection.hashes, ("c285110",))
        self.assertEqual(selection.python_versions, ("3.10",))
        self.assertEqual(selection.package_name, "kombu")

    def test_select_instances_accepts_specifier_strings(self):
        selection = select_instances_for_suite(
            "contrib::kombu",
            [
                RiotInstanceMetadata(hash="c285110", python_version="3.10", packages={"kombu": ">=5.2,<5.3"}),
                RiotInstanceMetadata(hash="1959ed5", python_version="3.11", packages={"kombu": ""}),
            ],
            filters=RiotSelectionFilters(package_version=">=5.2,<5.3"),
            integration_to_dependencies={"kombu": {"kombu"}},
        )

        self.assertEqual(selection.hashes, ("c285110",))
        self.assertEqual(selection.python_versions, ("3.10",))

    def test_select_instances_requires_explicit_latest_for_latest_rows(self):
        with self.assertRaisesRegex(ValueError, "matching '5.5.0'"):
            select_instances_for_suite(
                "contrib::kombu",
                [RiotInstanceMetadata(hash="1e82f55", python_version="3.10", packages={"kombu": ""})],
                filters=RiotSelectionFilters(package_version="5.5.0", python_version="3.10"),
                integration_to_dependencies={"kombu": {"kombu"}},
            )

    def test_select_instances_uses_registry_dependency_aliases(self):
        selection = select_instances_for_suite(
            "contrib::rediscluster",
            [RiotInstanceMetadata(hash="abc1234", python_version="3.11", packages={"redis-py-cluster": ">=2.0,<2.1"})],
            filters=RiotSelectionFilters(package_version="2.0.3", python_version="3.11"),
            integration_to_dependencies={"rediscluster": {"redis-py-cluster"}},
        )

        self.assertEqual(selection.hashes, ("abc1234",))
        self.assertEqual(selection.package_name, "redis-py-cluster")

    def test_select_instances_can_target_latest_rows(self):
        selection = select_instances_for_suite(
            "contrib::kombu",
            [
                RiotInstanceMetadata(hash="12aafe0", python_version="3.9", packages={"kombu": ""}),
                RiotInstanceMetadata(hash="1e82f55", python_version="3.10", packages={"kombu": ""}),
                RiotInstanceMetadata(hash="c285110", python_version="3.10", packages={"kombu": ">=5.2,<5.3"}),
            ],
            filters=RiotSelectionFilters(package_version="latest"),
            integration_to_dependencies={"kombu": {"kombu"}},
        )

        self.assertEqual(selection.hashes, ("12aafe0", "1e82f55"))
        self.assertEqual(selection.python_versions, ("3.10", "3.9"))

    def test_registry_dependency_map_does_not_require_pyyaml(self):
        dependency_map = _load_integration_to_dependency_map()

        self.assertIn("kombu", dependency_map["kombu"])
        self.assertIn("redis-py-cluster", dependency_map["rediscluster"])


if __name__ == "__main__":
    unittest.main()
