from generate_supported_versions import TestedVersion as VersionUnderTest
from generate_supported_versions import collect_tested_versions


def test_supported_version_generation_includes_mysql_minimum():
    tested_versions = collect_tested_versions()
    mysql_versions = tested_versions["mysql"]["mysql-connector-python"]

    assert VersionUnderTest(version="8.0.33", python_version="3.9") in mysql_versions
