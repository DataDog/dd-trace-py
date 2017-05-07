# stdlib
import time
import contextlib

# 3rd party
import psycopg2
from unittest import TestCase
from nose.tools import eq_, ok_, assert_raises
from nose.plugins.attrib import attr

from sqlalchemy.exc import OperationalError
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
from ddtrace.ext import sql as sqlx
from ddtrace.ext import errors as errorsx
from ddtrace.ext import net as netx

# testing
from ..config import POSTGRES_CONFIG
from ...test_tracer import get_dummy_tracer, DummyWriter


Base = declarative_base()


class Player(Base):
    """Player entity used to test SQLAlchemy ORM"""
    __tablename__ = 'players'

    id = Column(Integer, primary_key=True)
    name = Column(String)


class SQLiteTestCase(TestCase):
    """Testing SQLite engine"""

    def create_engine(self, engine_args, meta):
        # create a SQLAlchemy engine
        url = engine_args.pop('url')
        return create_engine(url, **engine_args)

    @contextlib.contextmanager
    def connection(self):
        # context manager that provides a connection
        # to the underlying database
        try:
            conn = self.engine.connect()
            yield conn
        finally:
            conn.close()

    def setUp(self):
        # TODO: move these at class level?
        self.vendor = 'sqlite'
        self.meta = {sqlx.DB: ':memory:'}
        self.engine_args = {'url': 'sqlite:///:memory:'}
        # create an engine
        self.engine = self.create_engine(self.engine_args, self.meta)

        # create the database / entities and prepare a session for the test
        Base.metadata.create_all(self.engine)
        Session = sessionmaker(bind=self.engine)
        self.session = Session()

        # trace the engine
        self.tracer = get_dummy_tracer()
        trace_engine(self.engine, self.tracer, service='sqlite-foo')

    def tearDown(self):
        # clear the database and dispose the engine
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_orm_insert(self):
        # ensures that the ORM session is traced
        wayne = Player(id=1, name='wayne')
        self.session.add(wayne)
        self.session.commit()

        traces = self.tracer.writer.pop_traces()
        # trace composition
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)
        span = traces[0][0]
        # span fields
        eq_(span.name, 'sqlite.query')
        eq_(span.service, 'sqlite-foo')
        eq_(span.resource, 'INSERT INTO players (id, name) VALUES (?, ?)')
        eq_(span.get_tag('sql.db'), ':memory:')
        eq_(span.get_tag('sql.rows'), '1')
        eq_(span.span_type, 'sql')
        eq_(span.error, 0)
        ok_(span.duration > 0)

    def test_session_query(self):
        # ensures that the Session queries are traced
        out = list(self.session.query(Player).filter_by(name='wayne'))
        eq_(len(out), 0)

        traces = self.tracer.writer.pop_traces()
        # trace composition
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)
        span = traces[0][0]
        # span fields
        eq_(span.name, 'sqlite.query')
        eq_(span.service, 'sqlite-foo')
        eq_(span.resource, 'SELECT players.id AS players_id, players.name AS players_name \nFROM players \nWHERE players.name = ?')
        eq_(span.get_tag('sql.db'), ':memory:')
        ok_(span.get_tag('sql.rows') is None)
        eq_(span.span_type, 'sql')
        eq_(span.error, 0)
        ok_(span.duration > 0)

    def test_engine_connect_execute(self):
        # ensures that engine.connect() is properly traced
        with self.connection() as conn:
            rows = conn.execute('SELECT * FROM players').fetchall()
            eq_(len(rows), 0)

        traces = self.tracer.writer.pop_traces()
        # trace composition
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)
        span = traces[0][0]
        # span fields
        eq_(span.name, 'sqlite.query')
        eq_(span.service, 'sqlite-foo')
        eq_(span.resource, 'SELECT * FROM players')
        eq_(span.get_tag('sql.db'), ':memory:')
        ok_(span.get_tag('sql.rows') is None)
        eq_(span.span_type, 'sql')
        eq_(span.error, 0)
        ok_(span.duration > 0)

    def test_engine_execute_errors(self):
        # ensures that SQL errors are reported
        with assert_raises(OperationalError) as ex:
            with self.connection() as conn:
                conn.execute('SELECT * FROM a_wrong_table').fetchall()

        traces = self.tracer.writer.pop_traces()
        # trace composition
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)
        span = traces[0][0]
        # span fields
        eq_(span.name, 'sqlite.query')
        eq_(span.service, 'sqlite-foo')
        eq_(span.resource, 'SELECT * FROM a_wrong_table')
        eq_(span.get_tag('sql.db'), ':memory:')
        ok_(span.get_tag('sql.rows') is None)
        eq_(span.span_type, 'sql')
        ok_(span.duration > 0)
        # check the error
        eq_(span.error, 1)
        eq_(span.get_tag('error.msg'), 'no such table: a_wrong_table')
        ok_('OperationalError' in span.get_tag('error.type'))
        ok_('OperationalError: no such table: a_wrong_table' in span.get_tag('error.stack'))

    def test_traced_service(self):
        # ensures that the service is set as expected
        services = self.tracer.writer.pop_services()
        expected = {
            'sqlite-foo': {'app': self.vendor, 'app_type': 'db'}
        }
        eq_(services, expected)


@attr('postgres')
def test_postgres():
    u = 'postgresql://%(user)s:%(password)s@%(host)s:%(port)s/%(dbname)s' % POSTGRES_CONFIG
    engine_args = {'url' : u}
    meta = {
        sqlx.DB: POSTGRES_CONFIG["dbname"],
        netx.TARGET_HOST: POSTGRES_CONFIG['host'],
        netx.TARGET_PORT: str(POSTGRES_CONFIG['port']),
    }

    _test_create_engine(engine_args, "pg-foo", "postgres", meta)


@attr('postgres')
def test_postgres_creator_func():
    def _creator():
        return psycopg2.connect(**POSTGRES_CONFIG)

    engine_args = {'url' : 'postgresql://', 'creator' : _creator}

    meta = {
        netx.TARGET_HOST: POSTGRES_CONFIG['host'],
        netx.TARGET_PORT: str(POSTGRES_CONFIG['port']),
        sqlx.DB: POSTGRES_CONFIG["dbname"],
    }

    _test_create_engine(engine_args, "pg-foo", "postgres", meta)


def _test_create_engine(engine_args, service, vendor, expected_meta):
    url = engine_args.pop("url")
    engine = create_engine(url, **engine_args)
    try:
        _test_engine(engine, service, vendor, expected_meta)
    finally:
        engine.dispose()


def _test_engine(engine, service, vendor, expected_meta):
    """ a test suite for various sqlalchemy engines. """
    tracer = Tracer()
    tracer.writer = DummyWriter()

    # create an engine and start tracing.
    trace_engine(engine, tracer, service=service)
    start = time.time()

    @contextlib.contextmanager
    def _connect():
        try:
            conn = engine.connect()
            yield conn
        finally:
            conn.close()

    with _connect() as conn:
        try:
            conn.execute("delete from players")
        except Exception:
            pass

    # boilerplate
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # do an ORM insert
    wayne = Player(id=1, name="wayne")
    session.add(wayne)
    session.commit()

    out = list(session.query(Player).filter_by(name="nothing"))
    eq_(len(out), 0)

    # do a regular old query that works
    with _connect() as conn:
        rows = conn.execute("select * from players").fetchall()
        eq_(len(rows), 1)
        eq_(rows[0]['name'], 'wayne')

    with _connect() as conn:
        try:
            conn.execute("select * from foo_Bah_blah")
        except Exception:
            pass
        else:
            assert 0

    end = time.time()

    spans = tracer.writer.pop()
    for span in spans:
        eq_(span.name, "%s.query" % vendor)
        eq_(span.service, service)
        eq_(span.span_type, "sql")

        for k, v in expected_meta.items():
            eq_(span.meta[k], v)

        # FIXME[matt] could be finer grained but i'm lazy
        assert start < span.start < end
        assert span.duration
        assert span.duration < end - start

    by_rsc = {s.resource:s for s in spans}

    # ensure errors work
    s = by_rsc["select * from foo_Bah_blah"]
    eq_(s.error, 1)
    assert "foo_Bah_blah" in s.get_tag(errorsx.ERROR_MSG)
    assert "foo_Bah_blah" in s.get_tag(errorsx.ERROR_STACK)

    expected = [
        "select * from players",
        "select * from foo_Bah_blah",
    ]

    for i in expected:
        assert i in by_rsc, "%s not in %s" % (i, by_rsc.keys())

    # ensure we have the service types
    services = tracer.writer.pop_services()
    expected = {
        service : {"app":vendor, "app_type":"db"}
    }
    eq_(services, expected)
