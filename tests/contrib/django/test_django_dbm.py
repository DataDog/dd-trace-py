import contextlib
from django.db import connections
import mock
import pytest

from ddtrace.contrib.django.patch import patch as django_patch
from ddtrace.contrib.django.patch import unpatch as django_unpatch
from tests.contrib import shared_tests
from tests.utils import DummyTracer
from tests.utils import override_env
from tests.utils import TracerTestCase

from ...contrib.config import POSTGRES_CONFIG


POSTGRES_CONFIG["db"] = POSTGRES_CONFIG["dbname"]


@contextlib.contextmanager
def override_global_tracer(tracer=None):
    import ddtrace
    original = ddtrace.tracer
    ddtrace.tracer = tracer
    try:
        yield
    finally:
        ddtrace.tracer = original


@pytest.mark.usefixtures("transactional_db", "tracer")
class PsycopgCore(TracerTestCase):
    # default service
    TEST_SERVICE = "postgres"

    def setUp(self):
        super(PsycopgCore, self).setUp()

        django_patch()

        # # If Django version >= 4.2.0, check if psycopg3 is installed,
        # # as we test Django>=4.2 with psycopg2 solely installed and not psycopg3 to ensure both work.
        # if django.VERSION < (4, 2, 0):
        #     pytest.skip(reason="Psycopg3 not supported in django<4.2")
        # else:
        from django.db import connections
        from ddtrace.contrib.psycopg.patch import patch

        patch()

        # # force recreate connection to ensure psycopg3 patching has occurred
        print(connections.all())
        del connections["postgres"]
        connections["postgres"].close()
        # breakpoint()
        connections["postgres"].connect()

    def tearDown(self):
        super(PsycopgCore, self).tearDown()

        from ddtrace.contrib.psycopg.patch import unpatch

        django_unpatch()
        unpatch()

    def get_cursor(self, service=None):
        conn = connections["postgres"]
        cursor = conn.cursor()

        return cursor

    def test_django_postgres_dbm_propagation_enabled(self):
        tracer = DummyTracer()
        with override_env(dict(DD_DBM_PROPAGATION_MODE="full")):
            with override_global_tracer(tracer):
                from ddtrace.internal import core
                from ddtrace.propagation._database_monitoring import handle_dbm_injection
                from ddtrace.settings._database_monitoring import DatabaseMonitoringConfig, dbm_config
                dbm_config.propagation_mode = "full"

                core.on("dbapi.execute", handle_dbm_injection, "result")
                core.on("psycopg.execute", handle_dbm_injection, "result")

                cursor = self.get_cursor()
                shared_tests._test_dbm_propagation_enabled(tracer, cursor, "postgres")
