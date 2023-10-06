import os

import mock

from ddtrace.internal import core
from ddtrace.internal.accupath.context_propagation import inject_context
from ddtrace.internal.accupath.context_propagation import extract_context
from ddtrace.internal.accupath.context_propagation import (
    HTTP_HEADER_ACCUPATH_PARENT_ID,
    HTTP_HEADER_ACCUPATH_PARENT_TIME,
    HTTP_HEADER_ACCUPATH_PATH_ID,
    HTTP_HEADER_ACCUPATH_ROOT_ID,
    HTTP_HEADER_ACCUPATH_ROOT_TIME)
from ddtrace.internal.accupath.node_info import NodeInfo, ROOT_NODE_REQUEST_OUT_TIME, ROOT_NODE_ID, PARENT_NODE_ID, PARENT_NODE_REQUEST_OUT_TIME


@mock.patch("time.time", mock.MagicMock(return_value=10))
@mock.patch.dict(os.environ, {"DD_SERVICE": "test-service", "DD_ENV": "test-env", "DD_HOSTNAME": "test-hostname"})
def test_context_injection_with_parent(fake_root_info):
    headers = dict()
    core.set_item(ROOT_NODE_REQUEST_OUT_TIME, 50)
    core.set_item(ROOT_NODE_ID, fake_root_info)
    inject_context(headers)

    expected_node_info = NodeInfo.from_local_env()
    parent_node_info = NodeInfo.from_string_dict(headers[HTTP_HEADER_ACCUPATH_PARENT_ID])
    root_node_info = NodeInfo.from_string_dict(headers[HTTP_HEADER_ACCUPATH_ROOT_ID])

    assert parent_node_info == expected_node_info

    assert headers[HTTP_HEADER_ACCUPATH_PARENT_TIME] == "10"
    assert headers[HTTP_HEADER_ACCUPATH_ROOT_TIME] == "50"
    assert headers[HTTP_HEADER_ACCUPATH_PATH_ID] == "15923206752587754541"
    assert root_node_info == fake_root_info 


@mock.patch("time.time", mock.MagicMock(return_value=10))
@mock.patch.dict(os.environ, {"DD_SERVICE": "test-service", "DD_ENV": "test-env", "DD_HOSTNAME": "test-hostname"})
def test_context_injection_without_parent(fake_node_info):
    headers = dict()
    inject_context(headers)

    expected_node_info = NodeInfo.from_local_env()
    parent_node_info = NodeInfo.from_string_dict(headers[HTTP_HEADER_ACCUPATH_PARENT_ID])
    root_node_info = NodeInfo.from_string_dict(headers[HTTP_HEADER_ACCUPATH_ROOT_ID])

    assert parent_node_info == expected_node_info

    assert headers[HTTP_HEADER_ACCUPATH_PARENT_TIME] == "10"
    assert headers[HTTP_HEADER_ACCUPATH_ROOT_TIME] == "10"
    assert headers[HTTP_HEADER_ACCUPATH_PATH_ID] == "15923206752587754541"
    assert root_node_info == fake_node_info


@mock.patch("time.time", mock.MagicMock(return_value=10))
@mock.patch.dict(os.environ, {"DD_SERVICE": "test-service", "DD_ENV": "test-env", "DD_HOSTNAME": "test-hostname"})
def test_context_extraction(fake_root_info, fake_parent_info):
    headers = {
        HTTP_HEADER_ACCUPATH_PARENT_ID: fake_parent_info.to_string_dict(),
        HTTP_HEADER_ACCUPATH_PARENT_TIME: "10",
        HTTP_HEADER_ACCUPATH_PATH_ID: "123456",
        HTTP_HEADER_ACCUPATH_ROOT_ID: fake_root_info.to_string_dict(),
        HTTP_HEADER_ACCUPATH_ROOT_TIME: "50",
    }
    extract_context(headers)

    assert core.get_item(ROOT_NODE_REQUEST_OUT_TIME) == 50
    assert core.get_item(ROOT_NODE_ID) == fake_root_info
    assert core.get_item(PARENT_NODE_ID) == fake_parent_info
    assert core.get_item(PARENT_NODE_REQUEST_OUT_TIME) == 10
