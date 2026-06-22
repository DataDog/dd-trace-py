import pytest

from ddtrace.contrib.internal.azure_cosmos.utils import normalize_resource_uri


@pytest.mark.parametrize(
    "uri,expected",
    [
        ("", ""),
        ("/dbs/myDb/colls/myColl", "/dbs/myDb/colls/myColl"),
        ("/dbs/myDb/colls/myColl/docs/item1/", "/dbs/myDb/colls/myColl/docs/?/"),
        ("/dbs/myDb/colls/myColl/docs/", "/dbs/myDb/colls/myColl/docs/"),
        ("/dbs/myDb/users/user1/permissions/perm1", "/dbs/myDb/users/?/permissions/?"),
        ("dbs/myDb/colls/myColl/docs/item1", "dbs/myDb/colls/myColl/docs/?"),
    ],
)
def test_normalize_resource_uri(uri, expected):
    assert normalize_resource_uri(uri) == expected
