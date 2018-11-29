import unittest
import mock

from ddtrace import Pin, Span
from ddtrace.contrib.dbapi import TracedCursor, TracedConnection
from ddtrace.ext import AppTypes, sql
from tests.test_tracer import get_dummy_tracer


class TestTracedCursor(unittest.TestCase):

    def setUp(self):
        self.cursor = mock.Mock()
        self.tracer = get_dummy_tracer()

    def test_execute_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        pin = Pin('pin_name', tracer=self.tracer)
        traced_cursor = TracedCursor(cursor, pin)
        assert traced_cursor is traced_cursor.execute('__query__', 'arg_1', kwarg1='kwarg1')
        cursor.execute.assert_called_once_with('__query__', 'arg_1', kwarg1='kwarg1')

    def test_executemany_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        pin = Pin('pin_name', tracer=self.tracer)
        traced_cursor = TracedCursor(cursor, pin)
        assert traced_cursor is traced_cursor.executemany('__query__', 'arg_1', kwarg1='kwarg1')
        cursor.executemany.assert_called_once_with('__query__', 'arg_1', kwarg1='kwarg1')

    def test_fetchone_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.fetchone.return_value = '__result__'
        pin = Pin('pin_name', tracer=self.tracer)
        traced_cursor = TracedCursor(cursor, pin)
        assert '__result__' == traced_cursor.fetchone('arg_1', kwarg1='kwarg1')
        cursor.fetchone.assert_called_once_with('arg_1', kwarg1='kwarg1')

    def test_fetchall_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.fetchall.return_value = '__result__'
        pin = Pin('pin_name', tracer=self.tracer)
        traced_cursor = TracedCursor(cursor, pin)
        assert '__result__' == traced_cursor.fetchall('arg_1', kwarg1='kwarg1')
        cursor.fetchall.assert_called_once_with('arg_1', kwarg1='kwarg1')

    def test_fetchmany_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        tracer = self.tracer
        cursor.rowcount = 0
        cursor.fetchmany.return_value = '__result__'
        pin = Pin('pin_name', tracer=self.tracer)
        traced_cursor = TracedCursor(cursor, pin)
        assert '__result__' == traced_cursor.fetchmany('arg_1', kwarg1='kwarg1')
        cursor.fetchmany.assert_called_once_with('arg_1', kwarg1='kwarg1')

    def test_correct_span_names(self):
        cursor = self.cursor
        tracer = self.tracer
        cursor.rowcount = 0
        pin = Pin('pin_name', tracer=tracer)
        traced_cursor = TracedCursor(cursor, pin)

        traced_cursor.execute('arg_1', kwarg1='kwarg1')
        assert tracer.writer.pop()[0].name == 'sql.query'

        traced_cursor.executemany('arg_1', kwarg1='kwarg1')
        assert tracer.writer.pop()[0].name == 'sql.query'

        traced_cursor.callproc('arg_1', 'arg2')
        assert tracer.writer.pop()[0].name == 'sql.query'

        traced_cursor.fetchone('arg_1', kwarg1='kwarg1')
        assert tracer.writer.pop()[0].name == 'sql.query.fetchone'

        traced_cursor.fetchmany('arg_1', kwarg1='kwarg1')
        assert tracer.writer.pop()[0].name == 'sql.query.fetchmany'

        traced_cursor.fetchall('arg_1', kwarg1='kwarg1')
        assert tracer.writer.pop()[0].name == 'sql.query.fetchall'

    def test_correct_span_names_can_be_overridden_by_pin(self):
        cursor = self.cursor
        tracer = self.tracer
        cursor.rowcount = 0
        pin = Pin('pin_name', app='changed', tracer=tracer)
        traced_cursor = TracedCursor(cursor, pin)

        traced_cursor.execute('arg_1', kwarg1='kwarg1')
        assert tracer.writer.pop()[0].name == 'changed.query'

        traced_cursor.executemany('arg_1', kwarg1='kwarg1')
        assert tracer.writer.pop()[0].name == 'changed.query'

        traced_cursor.callproc('arg_1', 'arg2')
        assert tracer.writer.pop()[0].name == 'changed.query'

        traced_cursor.fetchone('arg_1', kwarg1='kwarg1')
        assert tracer.writer.pop()[0].name == 'changed.query.fetchone'

        traced_cursor.fetchmany('arg_1', kwarg1='kwarg1')
        assert tracer.writer.pop()[0].name == 'changed.query.fetchmany'

        traced_cursor.fetchall('arg_1', kwarg1='kwarg1')
        assert tracer.writer.pop()[0].name == 'changed.query.fetchall'

    def test_when_pin_disabled_then_no_tracing(self):
        cursor = self.cursor
        tracer = self.tracer
        cursor.rowcount = 0
        tracer.enabled = False
        pin = Pin('pin_name', tracer=tracer)
        traced_cursor = TracedCursor(cursor, pin)

        assert traced_cursor is traced_cursor.execute('arg_1', kwarg1='kwarg1')
        assert len(tracer.writer.pop()) == 0

        assert traced_cursor is traced_cursor.executemany('arg_1', kwarg1='kwarg1')
        assert len(tracer.writer.pop()) == 0

        cursor.callproc.return_value = 'callproc'
        assert 'callproc' == traced_cursor.callproc('arg_1', 'arg_2')
        assert len(tracer.writer.pop()) == 0

        cursor.fetchone.return_value = 'fetchone'
        assert 'fetchone' == traced_cursor.fetchone('arg_1', 'arg_2')
        assert len(tracer.writer.pop()) == 0

        cursor.fetchmany.return_value = 'fetchmany'
        assert 'fetchmany' == traced_cursor.fetchmany('arg_1', 'arg_2')
        assert len(tracer.writer.pop()) == 0

        cursor.fetchall.return_value = 'fetchall'
        assert 'fetchall' == traced_cursor.fetchall('arg_1', 'arg_2')
        assert len(tracer.writer.pop()) == 0

    def test_span_info(self):
        cursor = self.cursor
        tracer = self.tracer
        cursor.rowcount = 123
        pin = Pin('my_service', app='my_app', tracer=tracer, tags={'pin1': 'value_pin1'})
        traced_cursor = TracedCursor(cursor, pin)

        def method():
            pass

        traced_cursor._trace_method(method, 'my_name', 'my_resource', {'extra1': 'value_extra1'})
        span = tracer.writer.pop()[0]  # type: Span
        assert span.meta['pin1'] == 'value_pin1', 'Pin tags are preserved'
        assert span.meta['extra1'] == 'value_extra1', 'Extra tags are merged into pin tags'
        assert span.name == 'my_name', 'Span name is respected'
        assert span.service == 'my_service', 'Service from pin'
        assert span.resource == 'my_resource', 'Resource is respected'
        assert span.span_type == 'sql', 'Span has the correct span type'
        # Row count
        assert span.get_metric('db.rowcount') == 123, 'Row count is set as a metric'
        assert span.get_tag('sql.rows') == '123', 'Row count is set as a tag (for legacy django cursor replacement)'

    def test_django_traced_cursor_backward_compatibility(self):
        cursor = self.cursor
        tracer = self.tracer
        # Django integration used to have its own TracedCursor implementation. When we replaced such custom
        # implementation with the generic dbapi traced cursor, we had to make sure to add the tag 'sql.rows' that was
        # set by the legacy replaced implementation.
        cursor.rowcount = 123
        pin = Pin('my_service', app='my_app', tracer=tracer, tags={'pin1': 'value_pin1'})
        traced_cursor = TracedCursor(cursor, pin)

        def method():
            pass

        traced_cursor._trace_method(method, 'my_name', 'my_resource', {'extra1': 'value_extra1'})
        span = tracer.writer.pop()[0]  # type: Span
        # Row count
        assert span.get_metric('db.rowcount') == 123, 'Row count is set as a metric'
        assert span.get_tag('sql.rows') == '123', 'Row count is set as a tag (for legacy django cursor replacement)'

class TestTracedConnection(unittest.TestCase):

    def setUp(self):
        self.connection = mock.Mock()
        self.tracer = get_dummy_tracer()

    def test_commit_is_traced(self):
        connection = self.connection
        tracer = self.tracer
        connection.commit.return_value = None
        pin = Pin('pin_name', tracer=tracer)
        traced_connection = TracedConnection(connection, pin)
        traced_connection.commit()
        assert tracer.writer.pop()[0].name == 'mock.connection.commit'
        connection.commit.assert_called_with()

    def test_rollback_is_traced(self):
        connection = self.connection
        tracer = self.tracer
        connection.rollback.return_value = None
        pin = Pin('pin_name', tracer=tracer)
        traced_connection = TracedConnection(connection, pin)
        traced_connection.rollback()
        assert tracer.writer.pop()[0].name == 'mock.connection.rollback'
        connection.rollback.assert_called_with()
