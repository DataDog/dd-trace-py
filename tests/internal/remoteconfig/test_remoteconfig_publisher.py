# -*- coding: utf-8 -*-
import mock

from ddtrace.internal.remoteconfig import ConfigMetadata
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig._publishers import RemoteConfigPublisher
from tests.internal.remoteconfig.utils import MockConnector


def test_remoteconfig_publisher_dispatch():
    mock_preprocess_results = mock.MagicMock()
    mock_preprocess_results.return_value = [{"config": "parsed_data"}]
    mock_connector = MockConnector({"example": "data"})

    publisher = RemoteConfigPublisher(mock_connector, mock_preprocess_results)
    config = {"id": "1", "product_name": "MOCK", "sha256_hash": "sha256_hash", "length": 5, "tuf_version": 35}
    publisher.append({"config": "data"}, "path", config)
    publisher.dispatch(None)

    if publisher._preprocess_results_func:
        mock_preprocess_results.assert_called_once_with(
            [
                Payload(
                    metadata=ConfigMetadata(
                        id="1",
                        product_name="MOCK",
                        sha256_hash="sha256_hash",
                        length=5,
                        tuf_version=35,
                        apply_state=1,
                        apply_error=None,
                    ),
                    path="path",
                    content={"config": "data"},
                )
            ],
            None,
        )
    assert mock_connector.data == [{"config": "parsed_data"}]
