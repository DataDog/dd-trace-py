# -*- coding: utf-8 -*-
import mock

from ddtrace.internal.remoteconfig._publishers import RemoteConfigPublisher
from ddtrace.internal.remoteconfig._publishers import RemoteConfigPublisherMergeDicts
from tests.internal.remoteconfig.utils import MockConnector


def test_remoteconfig_publisher_dispatch():
    mock_preprocess_results = mock.MagicMock()
    mock_preprocess_results.return_value = {"config": "parsed_data"}
    mock_connector = MockConnector({"example": "data"})

    publisher = RemoteConfigPublisher(mock_connector, mock_preprocess_results)
    publisher.append({"config": "data"}, "", {})
    publisher.dispatch()
    # TODO: RemoteConfigPublisher doesn't need _preprocess_results_func callback at this moment. Uncomment those
    #  lines if a new product need it
    #  if self._preprocess_results_func:
    #     config = self._preprocess_results_func(config, pubsub_instance)
    #  mock_preprocess_results.assert_called_once_with({"config": "data"}, None)

    assert mock_connector.data == [{"config": "data"}]
    assert mock_connector.metadata == [None]


def test_remoteconfig_publisher_merge_first_dispatch_lists():
    mock_connector = MockConnector({"example": "data"})

    publisher = RemoteConfigPublisherMergeDicts(mock_connector, None)
    publisher.append(
        {
            "config": [
                "data1",
            ]
        },
        "target_a",
        None,
    )
    publisher.append(
        {
            "config": [
                "data2",
            ]
        },
        "target_b",
        None,
    )

    publisher.dispatch()
    expected_result = mock_connector.data
    expected_result["config"].sort()
    assert expected_result == {"config": ["data1", "data2"]}
    assert mock_connector.metadata == {}


def test_remoteconfig_publisher_merge_first_dispatch_dicts():
    mock_connector = MockConnector({})

    publisher = RemoteConfigPublisherMergeDicts(mock_connector, None)
    publisher.append({"config": {"c": "d"}}, "target_b", None)

    publisher.dispatch()

    expected_result = mock_connector.data
    assert expected_result == {"config": {"c": "d"}}
    assert mock_connector.metadata == {}
