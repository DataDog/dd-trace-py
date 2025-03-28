# -*- coding: utf-8 -*-
import pytest

from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector
from tests.utils import remote_config_build_payload as build_payload


def any_product(data):
    return [build_payload("ANY_PRODUCT", data, "any_path", id_based_on_content=True)]


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
    assert isinstance(connector._hash_config(any_product(data)), int)


def test_connector():
    connector = PublisherSubscriberConnector()
    connector.write(any_product({"a": "b"}))
    assert connector.read()[0].content == {"a": "b"}


def test_connector_multiple_reads_no_new_data():
    connector = PublisherSubscriberConnector()
    connector.write(any_product({"a": "b"}))
    assert connector.read()[0].content == {"a": "b"}
    assert connector.read() == []


def test_connector_multiple_reads_same_data():
    connector = PublisherSubscriberConnector()
    payload = any_product({"a": "b"})
    connector.write(payload)
    assert connector.read()[0].content == {"a": "b"}
    connector.write(payload)
    assert connector.read() == []


def test_connector_multiple_reads_different_data():
    connector = PublisherSubscriberConnector()
    connector.write(any_product({"a": "b"}))
    assert connector.read()[0].content == {"a": "b"}
    connector.write(any_product({"c": "d"}))
    assert connector.read()[0].content == {"c": "d"}
    connector.write(any_product({"a": "b"}))
    assert connector.read()[0].content == {"a": "b"}


global_connector = PublisherSubscriberConnector()


def test_write_read():
    global_connector.write(any_product({"data": "1"}))
    assert global_connector.read()[0].content == {"data": "1"}
    global_connector.write(any_product({"data": "1"}))
    assert global_connector.read() == []
    global_connector.write(any_product({"data": "2"}))
    assert global_connector.read()[0].content == {"data": "2"}
    global_connector.write(any_product({"data": "3"}))
    assert global_connector.read()[0].content == {"data": "3"}
    global_connector.write(any_product({"data": "3"}))
    assert global_connector.read() == []
    global_connector.write(any_product({"data": "3"}))
    assert global_connector.read() == []
    global_connector.write(any_product({"data": "4"}))
    assert global_connector.read()[0].content == {"data": "4"}
    global_connector.write(any_product({"data": "4"}))
    assert global_connector.read() == []
