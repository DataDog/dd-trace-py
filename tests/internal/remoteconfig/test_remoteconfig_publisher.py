# -*- coding: utf-8 -*-
import mock

from ddtrace.internal.remoteconfig._publishers import RemoteConfigPublisher
from ddtrace.internal.remoteconfig._publishers import RemoteConfigPublisherMergeFirst
from tests.internal.remoteconfig.utils import MockConnector


def test_remoteconfig_publisher_dispatch():
    mock_preprocess_results = mock.MagicMock()
    mock_preprocess_results.return_value = {"config": "parsed_data"}
    mock_connector = MockConnector({"example": "data"})

    publisher = RemoteConfigPublisher(mock_connector, mock_preprocess_results)
    publisher.dispatch(config={"config": "data"}, metadata={})
    mock_preprocess_results.assert_called_once_with({"config": "data"}, None)

    assert mock_connector.data == {"config": "parsed_data"}
    assert mock_connector.metadata == {}


def test_remoteconfig_publisher_merge_first_dispatch_lists():
    mock_connector = MockConnector({"example": "data"})

    publisher = RemoteConfigPublisherMergeFirst(mock_connector, None)
    publisher.append(
        "target_a",
        {
            "config": [
                "data1",
            ]
        },
    )
    publisher.append(
        "target_b",
        {
            "config": [
                "data2",
            ]
        },
    )

    publisher.dispatch()
    expected_result = mock_connector.data
    expected_result["config"].sort()
    assert expected_result == {"config": ["data1", "data2"]}
    assert mock_connector.metadata == {}


def test_remoteconfig_publisher_merge_first_dispatch_dicts():
    mock_connector = MockConnector({})

    publisher = RemoteConfigPublisherMergeFirst(mock_connector, None)
    publisher.append("target_b", {"config": {"c": "d"}})

    publisher.dispatch()

    expected_result = mock_connector.data
    assert expected_result == {"config": {"c": "d"}}
    assert mock_connector.metadata == {}
