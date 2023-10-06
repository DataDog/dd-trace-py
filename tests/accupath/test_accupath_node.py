import json
import os
import time

import mock
import pytest

from ddtrace.internal import core
from ddtrace.internal.accupath.node_info import NodeInfo
from ddtrace.internal.accupath.node_info import ROOT_NODE_ID


@mock.patch.dict(os.environ, {"DD_SERVICE": "test-service", "DD_ENV": "test-env", "DD_HOSTNAME": "test-hostname"})
def test_generate_current_service_node_id_v0(fake_node_info):
    output = NodeInfo.from_local_env()

    assert output == fake_node_info, "Should match expected output"


def test_service_node_to_hash_v0(fake_node_info):
    output = fake_node_info.to_hash()

    assert output == 12792889021570308237


def test_service_node_to_bytes_v0(fake_node_info):
    output = fake_node_info.to_bytes()

    assert output == b"test-servicetest-envtest-hostname"


@mock.patch.dict(os.environ, {"DD_SERVICE": "test-service", "DD_ENV": "test-env", "DD_HOSTNAME": "test-hostname"})
def test_generate_root_node_id_v0(fake_node_info, fake_root_info):
    no_prior_parent_output = NodeInfo.get_root_node_info()
    assert no_prior_parent_output == fake_node_info, "Should match expected output"


    core.set_item(ROOT_NODE_ID, fake_root_info)
    prior_parent_output = NodeInfo.get_root_node_info()

    assert prior_parent_output == fake_root_info, "Should match expected output"
