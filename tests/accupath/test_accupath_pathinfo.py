import os

import mock
import pytest

from ddtrace.internal import core
from ddtrace.internal.accupath.path_info import UPSTREAM_PATHWAY_ID, PathInfo
from ddtrace.internal.accupath.node_info import NodeInfo


def test_get_parent_path_id():
    output = PathInfo.get_upstream_pathway_id()
    assert output == 0

    core.set_item(UPSTREAM_PATHWAY_ID, 1)
    output = PathInfo.get_upstream_pathway_id()
    assert output == 1


@mock.patch.dict(os.environ, {"DD_SERVICE": "test-service", "DD_ENV": "test-env", "DD_HOSTNAME": "test-hostname"})
def test_from_local_env(fake_node_info):
    core.set_item(UPSTREAM_PATHWAY_ID, 2)
    pathway_info = PathInfo.from_local_env()
    assert pathway_info.current_node_info == fake_node_info, "values should match"
    assert pathway_info.tags == []
    assert pathway_info.upstream_pathway_id == 2


@mock.patch.dict(os.environ, {"DD_SERVICE": "test-service", "DD_ENV": "test-env", "DD_HOSTNAME": "test-hostname"})
def test_hash():
    PathInfo.from_local_env().to_hash()