
# 3p
from pymemcache.client.base import Client
import wrapt
from nose.tools import eq_

# project
from ddtrace import Pin
from ddtrace.contrib.pymemcache.trace import TracedClient
from tests.test_tracer import get_dummy_tracer


def test_trace():
    client, tracer = _get_test_client_tracer(service='foo')
    assert not tracer.writer.pop()

    commands = []

    client.set('some_key', 'some_value')
    result = client.get('some_key')
    assert result == 'some_value'
    commands.extend(_s('set get'))

    client.add('foo', 'bar')
    client.replace('foo', 'baz')
    eq_('baz', client.get('foo'))
    commands.extend(_s('add replace get'))

    client.set('append_k', 'foo')
    client.append('append_k', 'post')
    client.prepend('append_k', 'pre')
    eq_('prefoopost', client.get('append_k'))
    commands.extend(_s('set append prepend get'))

    spans = tracer.writer.pop()
    for s in spans:
        assert s.service == 'foo'
        assert s.name == 'memcached.cmd'

    resources = sorted(s.resource for s in spans)
    commands.sort()

    eq_(resources, commands)


def _get_test_client_tracer(service='test-memcached'):
    tracer = get_dummy_tracer()
    pin = Pin(service=service, tracer=tracer)
    client = TracedClient(('localhost', 11211))
    pin.onto(client)
    return client, tracer

def _s(word):
    return word.split(' ')
