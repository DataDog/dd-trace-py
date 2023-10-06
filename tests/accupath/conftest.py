import pytest 

from ddtrace.internal import core
from ddtrace.internal.accupath.node_info import NodeInfo, ROOT_NODE_REQUEST_OUT_TIME, ROOT_NODE_ID
from ddtrace.internal.accupath.path_info import PathInfo, UPSTREAM_PATHWAY_ID


@pytest.fixture
def fake_node_info():
    return NodeInfo("test-service", "test-env", "test-hostname")

@pytest.fixture
def fake_root_info():
    return NodeInfo("test-root-service", "test-root-env", "test-root-host")

@pytest.fixture
def fake_parent_info():
    return NodeInfo("test-parent-service", "test-parent-env", "test-parent-host")

@pytest.fixture(autouse=True)
def clean_context():
    for key in [ROOT_NODE_REQUEST_OUT_TIME, ROOT_NODE_ID, UPSTREAM_PATHWAY_ID]:
        _clean_var(key)

def _clean_var(key):
    if key in core._CURRENT_CONTEXT.get()._data:
        del core._CURRENT_CONTEXT.get()._data[key]
