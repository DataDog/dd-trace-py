# -*- coding: utf-8 -*-
import pytest

from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector


@pytest.mark.parametrize(
    "data",
    [
        {"data": "ʂ"},
        {"data": {"a": True}},
        {"data": {"😀": "🤣😅😁😇"}},
        [1, 2, 3, 4],
        {1, 2, 3, 4},
    ],
)
def test_hash(data):
    connector = PublisherSubscriberConnector()
    assert type(connector._hash_config(data, None)) is int


def test_connector():
    connector = PublisherSubscriberConnector()
    connector.write("", {"a": "b"})
    assert connector.read() == {"config": {"a": "b"}, "metadata": "", "shared_data_counter": 1}


def test_connector_multiple_reads_no_new_data():
    connector = PublisherSubscriberConnector()
    connector.write("", {"a": "b"})
    assert connector.read() == {"config": {"a": "b"}, "metadata": "", "shared_data_counter": 1}
    assert connector.read() == {}


def test_connector_multiple_reads_same_data():
    connector = PublisherSubscriberConnector()
    connector.write("", {"a": "b"})
    assert connector.read() == {"config": {"a": "b"}, "metadata": "", "shared_data_counter": 1}
    connector.write("", {"a": "b"})
    assert connector.read() == {}


def test_connector_multiple_reads_different_data():
    connector = PublisherSubscriberConnector()
    connector.write("", {"a": "b"})
    assert connector.read() == {"config": {"a": "b"}, "metadata": "", "shared_data_counter": 1}
    connector.write("", {"c": "d"})
    assert connector.read() == {"config": {"c": "d"}, "metadata": "", "shared_data_counter": 2}
    connector.write("", {"a": "b"})
    assert connector.read() == {"config": {"a": "b"}, "metadata": "", "shared_data_counter": 3}


global_connector = PublisherSubscriberConnector()


def test_write_read():
    global_connector.write("", {"data": "1"})
    assert global_connector.read() == {"config": {"data": "1"}, "metadata": "", "shared_data_counter": 1}
    global_connector.write("", {"data": "1"})
    assert global_connector.read() == {}
    global_connector.write("", {"data": "2"})
    assert global_connector.read() == {"config": {"data": "2"}, "metadata": "", "shared_data_counter": 2}
    global_connector.write("", {"data": "3"})
    assert global_connector.read() == {"config": {"data": "3"}, "metadata": "", "shared_data_counter": 3}
    global_connector.write("", {"data": "3"})
    assert global_connector.read() == {}
    global_connector.write("", {"data": "3"})
    assert global_connector.read() == {}
    global_connector.write("", {"data": "4"})
    assert global_connector.read() == {"config": {"data": "4"}, "metadata": "", "shared_data_counter": 4}
    global_connector.write("", {"data": "4"})
    assert global_connector.read() == {}
