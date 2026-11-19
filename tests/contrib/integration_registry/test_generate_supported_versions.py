from generate_supported_versions import collect_tested_versions


def test_supported_version_generation_attributes_direct_dependencies():
    tested_versions = collect_tested_versions()

    assert tested_versions["mysql"]["mysql-connector-python"]
