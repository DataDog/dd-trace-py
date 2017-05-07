# stdlib
import time
import contextlib

# 3rd party
import psycopg2
from unittest import TestCase
from nose.tools import eq_, ok_, assert_raises
from nose.plugins.attrib import attr

from sqlalchemy.exc import OperationalError, ProgrammingError
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


class SQLAlchemyTestMixin(object):
    """SQLAlchemy test mixin that adds many functionalities.
    TODO: document how to use it
    """
    VENDOR = None
    SQL_DB = None
    SERVICE = None
    ENGINE_ARGS = None

    def create_engine(self, engine_args):
        # create a SQLAlchemy engine
        config = dict(engine_args)
        url = config.pop('url')
        return create_engine(url, **config)

    @contextlib.contextmanager
    def connection(self):
        # context manager that provides a connection
        # to the underlying database
        try:
            conn = self.engine.connect()
            yield conn
        finally:
            conn.close()

    def check_meta(self, span):
        # function that can be implemented according to the
        # specific engine implementation
        return

    def setUp(self):
        # create an engine
        self.engine = self.create_engine(self.ENGINE_ARGS)

        # create the database / entities and prepare a session for the test
        Base.metadata.drop_all(bind=self.engine)
        Base.metadata.create_all(self.engine, checkfirst=False)
        Session = sessionmaker(bind=self.engine)
        self.session = Session()

        # trace the engine
        self.tracer = get_dummy_tracer()
        trace_engine(self.engine, self.tracer, service=self.SERVICE)

    def tearDown(self):
        # clear the database and dispose the engine
        self.session.close()
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
        eq_(span.name, '{}.query'.format(self.VENDOR))
        eq_(span.service, self.SERVICE)
        ok_('INSERT INTO players' in span.resource)
        eq_(span.get_tag('sql.db'), self.SQL_DB)
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
        eq_(span.name, '{}.query'.format(self.VENDOR))
        eq_(span.service, self.SERVICE)
        ok_('SELECT players.id AS players_id, players.name AS players_name \nFROM players \nWHERE players.name' in span.resource)
        eq_(span.get_tag('sql.db'), self.SQL_DB)
        eq_(span.get_tag('sql.rows'), '0')
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
        eq_(span.name, '{}.query'.format(self.VENDOR))
        eq_(span.service, self.SERVICE)
        eq_(span.resource, 'SELECT * FROM players')
        eq_(span.get_tag('sql.db'), self.SQL_DB)
        eq_(span.get_tag('sql.rows'), '0')
        eq_(span.span_type, 'sql')
        eq_(span.error, 0)
        ok_(span.duration > 0)

    def test_traced_service(self):
        # ensures that the service is set as expected
        services = self.tracer.writer.pop_services()
        expected = {
            self.SERVICE: {'app': self.VENDOR, 'app_type': 'db'}
        }
        eq_(services, expected)


class SQLiteTestCase(SQLAlchemyTestMixin, TestCase):
    """Testing SQLite engine"""
    VENDOR = 'sqlite'
    SQL_DB = ':memory:'
    SERVICE = 'sqlite-test'
    ENGINE_ARGS = {'url': 'sqlite:///:memory:'}

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
        eq_(span.name, '{}.query'.format(self.VENDOR))
        eq_(span.service, self.SERVICE)
        eq_(span.resource, 'SELECT * FROM a_wrong_table')
        eq_(span.get_tag('sql.db'), self.SQL_DB)
        ok_(span.get_tag('sql.rows') is None)
        eq_(span.span_type, 'sql')
        ok_(span.duration > 0)
        # check the error
        eq_(span.error, 1)
        eq_(span.get_tag('error.msg'), 'no such table: a_wrong_table')
        ok_('OperationalError' in span.get_tag('error.type'))
        ok_('OperationalError: no such table: a_wrong_table' in span.get_tag('error.stack'))


class PostgresTestCase(SQLAlchemyTestMixin, TestCase):
    """Testing Postgres engine"""
    VENDOR = 'postgres'
    SQL_DB = 'postgres'
    SERVICE = 'postgres-test'
    ENGINE_ARGS = {'url': 'postgresql://%(user)s:%(password)s@%(host)s:%(port)s/%(dbname)s' % POSTGRES_CONFIG}

    def check_meta(self, span):
        # check database connection tags
        eq_(span.get_tag('out.host'), POSTGRES_CONFIG['host'])
        eq_(span.get_tag('out.port'), str(POSTGRES_CONFIG['port']))

    def test_engine_execute_errors(self):
        # ensures that SQL errors are reported
        with assert_raises(ProgrammingError) as ex:
            with self.connection() as conn:
                conn.execute('SELECT * FROM a_wrong_table').fetchall()

        traces = self.tracer.writer.pop_traces()
        # trace composition
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)
        span = traces[0][0]
        # span fields
        eq_(span.name, '{}.query'.format(self.VENDOR))
        eq_(span.service, self.SERVICE)
        eq_(span.resource, 'SELECT * FROM a_wrong_table')
        eq_(span.get_tag('sql.db'), self.SQL_DB)
        ok_(span.get_tag('sql.rows') is None)
        self.check_meta(span)
        eq_(span.span_type, 'sql')
        ok_(span.duration > 0)
        # check the error
        eq_(span.error, 1)
        ok_('relation "a_wrong_table" does not exist' in span.get_tag('error.msg'))
        ok_('ProgrammingError' in span.get_tag('error.type'))
        ok_('ProgrammingError: relation "a_wrong_table" does not exist' in span.get_tag('error.stack'))


class PostgresCreatorTestCase(PostgresTestCase):
    """Testing Postgres with a specific creator function"""
    VENDOR = 'postgres'
    SQL_DB = 'postgres'
    SERVICE = 'postgres-test'
    ENGINE_ARGS = {'url': 'postgresql://', 'creator': lambda: psycopg2.connect(**POSTGRES_CONFIG)}
