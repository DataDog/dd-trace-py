# stdlib
import time

# 3p
from nose.tools import eq_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
)

# project
from ddtrace import Tracer
from ddtrace.contrib.sqlalchemy import trace_engine
from tests.test_tracer import DummyWriter
from tests.contrib.config import PG_CONFIG


Base = declarative_base()


class Player(Base):

    __tablename__ = 'players'

    id = Column(Integer, primary_key=True)
    name = Column(String)


def test_sqlite():
    _test_engine('sqlite:///:memory:', "sqlite-foo", "sqlite3")

def test_postgres():
    url = 'postgresql://%(user)s:%(password)s@%(host)s:%(port)s/%(dbname)s' % PG_CONFIG
    _test_engine(url, "pg-foo", "postgres")

def _test_engine(url, service, vendor):
    """ a test suite for various sqlalchemy engines. """
    tracer = Tracer()
    tracer.writer = DummyWriter()

    # create an engine and start tracing.
    engine = create_engine(url, echo=False)
    trace_engine(engine, tracer, service=service)
    start = time.time()


    conn = engine.connect()
    conn.execute("drop table if exists players")

    # boilerplate
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # do an ORM query
    wayne = Player(id=1, name="wayne")
    session.add(wayne)
    session.commit()

    # do a regular old query that works
    conn = engine.connect()
    rows = conn.execute("select * from players").fetchall()
    eq_(len(rows), 1)
    eq_(rows[0]['name'], 'wayne')

    try:
        conn.execute("select * from foo_Bah_blah")
    except Exception:
        pass

    end = time.time()

    spans = tracer.writer.pop()
    for span in spans:
        eq_(span.name, "%s.query" % vendor)
        eq_(span.service, service)
        eq_(span.span_type, "sql")
        if "sqlite" not in vendor:
            eq_(span.meta["sql.db"], PG_CONFIG["dbname"])
            eq_(span.meta["out.host"], PG_CONFIG["host"])
            eq_(span.meta["out.port"], str(PG_CONFIG["port"]))
        else:
            eq_(span.meta["sql.db"], ":memory:")

        assert start < span.start < end
        assert span.duration
        assert span.duration < end - start

