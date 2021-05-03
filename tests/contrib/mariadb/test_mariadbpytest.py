import pytest
import mariadb 
from tests.utils import DummyTracer
from tests.utils import assert_is_measured




@pytest.fixture
def tracer():
    tracer = DummyTracer()

    # Yield to our test
    yield tracer
    tracer.pop()


@pytest.fixture
def connection(tracer):
    connection = mariadb.connect(
            user="user",
            password="user",
            host="127.0.0.1",
            port=3306,
            database="employees"
        )
    Pin.override(connection, tracer=tracer)
#need to figure out where the code to make the db goes, previously it went in an init
#db file but now maybe i should just use the connection to create a database here?
#     CREATE DATABASE IF NOT EXISTS `test`;
# GRANT ALL ON `test`.* TO 'user'@'%';
    yield connection
    connection.close()


def test_simple_query(connection, tracer):
    cursor = connection.cursor()
    cursor.execute("SELECT 1")
    rows = cursor.fetchall()
    assert len(rows) == 1
    spans = tracer.pop()
    assert len(spans) == 1
    span = spans[0]
    assert_is_measured(span)
    assert span.service == "mariadb"
    assert span.name == "mariadb.query"
    assert span.span_type == "sql"
    assert span.error == 0
    assert span.get_metric("out.port") == 3306
