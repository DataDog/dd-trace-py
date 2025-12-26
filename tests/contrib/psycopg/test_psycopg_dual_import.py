import sys
from unittest.mock import Mock

from psycopg2 import ProgrammingError

from ddtrace.contrib.internal.psycopg.connection import Psycopg3TracedConnection
from ddtrace.contrib.internal.psycopg.connection import patch_conn
from ddtrace.contrib.internal.psycopg.patch import unpatch
from tests.utils import TracerTestCase


PSYCOPG_DSN_WITH_SSLCERTMODE = "dbname=test user=test host=localhost sslcertmode=allow"
MODULES_TO_CLEANUP = ["psycopg", "psycopg2"]


class MockPsycopg3Connection:
    """Mocking a psycopg connection because it's hard to set up a riot config that reproduces the real bug"""

    def __init__(self, dsn=PSYCOPG_DSN_WITH_SSLCERTMODE):
        self.info = Mock()
        self.info.dsn = dsn


def _create_mock_parser(original_parser):
    """Older libpq don't support sslcertmode so this mocks that"""

    def side_effect(dsn_string):
        if "sslcertmode" in dsn_string:
            raise ProgrammingError('invalid dsn: invalid connection option "sslcertmode"')
        return original_parser(dsn_string)

    mock = Mock(side_effect=side_effect)
    return mock


def _cleanup_modules():
    for mod in MODULES_TO_CLEANUP:
        if mod in sys.modules:
            del sys.modules[mod]


class TestPsycopgDualImport(TracerTestCase):
    """
    The purpose of these tests is to test the impact of importing psycopg2 and psycopg in different orders
    See https://github.com/DataDog/dd-trace-py/issues/9414
    """

    def setUp(self):
        super(TestPsycopgDualImport, self).setUp()
        unpatch()
        # Requires ddtrace.auto here to activate ModuleWatchdog and to patch all modules to trigger the erro
        import ddtrace.auto  # noqa: F401

        _cleanup_modules()

    def tearDown(self):
        super(TestPsycopgDualImport, self).tearDown()
        unpatch()
        _cleanup_modules()

    def test_dsn_parser_import_psycopg_first(self):
        """
        Current bug:
        If psycopg is imported, then psycopg2, the global parser is overwritten by psycopg2's parser.
        We need to test the scenario where psycopg2's parser is used on a psycopg3 DSN.
        """
        import psycopg  # noqa: F401
        import psycopg2  # noqa: F401
        from psycopg2.extensions import parse_dsn as psycopg2_parse_dsn

        from ddtrace.ext import sql

        mocked_psycopg2_parser = _create_mock_parser(psycopg2_parse_dsn)
        sql.parse_pg_dsn = mocked_psycopg2_parser

        mock_conn = MockPsycopg3Connection()

        patch_conn(mock_conn, Psycopg3TracedConnection)

    def test_dsn_parser_import_psycopg2_first(self):
        """
        Current bug:
        If psycopg2 is imported, then psycopg, the global parser is overwritten by psycopg's parser.
        But we need to test the scenario where psycopg2's parser is used on a psycopg3 DSN.
        """
        import psycopg2  # noqa: F401, I001
        import psycopg  # noqa: F401
        from psycopg2.extensions import parse_dsn as psycopg2_parse_dsn

        from ddtrace.ext import sql

        mocked_psycopg2_parser = _create_mock_parser(psycopg2_parse_dsn)
        sql.parse_pg_dsn = mocked_psycopg2_parser

        mock_conn = MockPsycopg3Connection()

        patch_conn(mock_conn, Psycopg3TracedConnection)
