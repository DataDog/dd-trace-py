import sqlalchemy
from sqlalchemy import text

from ddtrace.contrib.internal.sqlalchemy.patch import get_version
from ddtrace.contrib.internal.sqlalchemy.patch import patch
from ddtrace.contrib.internal.sqlalchemy.patch import unpatch
from ddtrace.trace import Pin
from tests.contrib.patch import emit_integration_and_version_to_test_agent
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured

from ..config import POSTGRES_CONFIG


class SQLAlchemyPatchTestCase(TracerTestCase):
    """TestCase that checks if the engine is properly traced
    when the `patch()` method is used.
    """

    def setUp(self):
        super(SQLAlchemyPatchTestCase, self).setUp()

        # create a traced engine with the given arguments
        # and configure the current PIN instance
        patch()
        dsn = "postgresql://%(user)s:%(password)s@%(host)s:%(port)s/%(dbname)s" % POSTGRES_CONFIG
        self.engine = sqlalchemy.create_engine(dsn)
        Pin._override(self.engine, tracer=self.tracer)

        # prepare a connection
        self.conn = self.engine.connect()

    def tearDown(self):
        super(SQLAlchemyPatchTestCase, self).tearDown()

        # clear the database and dispose the engine
        self.conn.close()
        self.engine.dispose()
        unpatch()

    def test_engine_traced(self):
        # ensures that the engine is traced
        rows = self.conn.execute(text("SELECT 1")).fetchall()
        assert len(rows) == 1

        traces = self.pop_traces()
        # trace composition
        assert len(traces) == 1
        assert len(traces[0]) == 1
        span = traces[0][0]
        # check subset of span fields
        assert_is_measured(span)
        assert span.name == "postgres.query"
        assert span.service == "postgres"
        assert span.error == 0
        assert span.duration > 0

    def test_engine_pin_service(self):
        # ensures that the engine service is updated with the PIN object
        Pin._override(self.engine, service="replica-db")
        rows = self.conn.execute(text("SELECT 1")).fetchall()
        assert len(rows) == 1

        traces = self.pop_traces()
        # trace composition
        assert len(traces) == 1
        assert len(traces[0]) == 1
        span = traces[0][0]
        # check subset of span fields
        assert_is_measured(span)
        assert span.name == "postgres.query"
        assert span.service == "replica-db"
        assert span.error == 0
        assert span.duration > 0

    def test_and_emit_get_version(self):
        version = get_version()
        assert type(version) == str
        assert version != ""

        emit_integration_and_version_to_test_agent("sqlalchemy", version)

    def test_long_sql_query(self):
        # Tests a long query with one column
        long_value = "nonsense query" * 1000000
        results = self.conn.execute(text("SELECT :x as newcolumn"), {"x": long_value}).fetchall()
        assert len(results) == 1

        traces = self.pop_traces()
        # trace composition
        assert len(traces) == 1
        assert len(traces[0]) == 1
        span = traces[0][0]
        # check subset of span fields
        assert_is_measured(span)
        assert span.name == "postgres.query"
        assert span.service == "postgres"
        assert span.resource == "SELECT %(x)s as newcolumn"

        # Tests a long query with multiple column
        long_query = "SELECT " + ", ".join(
            [
                f"'nonsense query many words over and over and over and over and over and over and over and over to make this super long and over and over' as column{i}"
                for i in range(1, 1000)
            ]
        )
        results = self.conn.execute(text(long_query)).fetchall()
        assert len(results) == 1
        traces = self.pop_traces()
        span = traces[0][0]
        assert span.name == "postgres.query"
        # The resulting resource name is super long
        assert len(span.resource) == 151745
