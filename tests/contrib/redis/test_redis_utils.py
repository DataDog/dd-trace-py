# -*- coding: utf-8 -*-
"""
Unit tests for ddtrace.contrib.internal.redis_utils helpers.

These tests use mocks and do not require a live Redis instance.
"""

from unittest import mock

from ddtrace.contrib.internal.redis_utils import _build_tags
from ddtrace.contrib.internal.redis_utils import _extract_cluster_node_conn_tags
from ddtrace.contrib.internal.redis_utils import _extract_conn_tags
from ddtrace.ext import net


# ---------------------------------------------------------------------------
# _extract_conn_tags
# ---------------------------------------------------------------------------


class TestExtractConnTags:
    def test_basic(self):
        tags = _extract_conn_tags({"host": "redis.example.com", "port": 6379, "db": 0})
        assert tags[net.TARGET_HOST] == "redis.example.com"
        assert tags[net.TARGET_PORT] == 6379
        assert tags[net.SERVER_ADDRESS] == "redis.example.com"

    def test_db_default_when_missing(self):
        tags = _extract_conn_tags({"host": "localhost", "port": 6379})
        from ddtrace.ext import redis as redisx

        assert tags[redisx.DB] == 0

    def test_db_default_when_none(self):
        tags = _extract_conn_tags({"host": "localhost", "port": 6379, "db": None})
        from ddtrace.ext import redis as redisx

        assert tags[redisx.DB] == 0

    def test_client_name_included_when_present(self):
        tags = _extract_conn_tags({"host": "localhost", "port": 6379, "client_name": "my-app"})
        from ddtrace.ext import redis as redisx

        assert tags[redisx.CLIENT_NAME] == "my-app"

    def test_client_name_absent_when_not_set(self):
        tags = _extract_conn_tags({"host": "localhost", "port": 6379})
        from ddtrace.ext import redis as redisx

        assert redisx.CLIENT_NAME not in tags

    def test_missing_host_returns_empty(self):
        tags = _extract_conn_tags({"port": 6379})
        assert tags == {}

    def test_missing_port_returns_empty(self):
        tags = _extract_conn_tags({"host": "localhost"})
        assert tags == {}

    def test_empty_dict_returns_empty(self):
        assert _extract_conn_tags({}) == {}

    def test_exception_returns_empty(self):
        # Passing a non-dict that raises on key access
        assert _extract_conn_tags(None) == {}  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _extract_cluster_node_conn_tags
# ---------------------------------------------------------------------------


class TestExtractClusterNodeConnTags:
    def _make_instance(self, host="redis-cluster.example.com", port=7000):
        node = mock.MagicMock()
        node.host = host
        node.port = port
        instance = mock.MagicMock()
        instance.get_default_node.return_value = node
        return instance

    def test_returns_host(self):
        tags = _extract_cluster_node_conn_tags(self._make_instance(host="myhost", port=7001))
        assert tags[net.TARGET_HOST] == "myhost"

    def test_returns_port(self):
        tags = _extract_cluster_node_conn_tags(self._make_instance(port=7002))
        assert tags[net.TARGET_PORT] == 7002

    def test_returns_server_address(self):
        tags = _extract_cluster_node_conn_tags(self._make_instance(host="myhost"))
        assert tags[net.SERVER_ADDRESS] == "myhost"

    def test_host_and_server_address_match(self):
        tags = _extract_cluster_node_conn_tags(self._make_instance(host="cluster.local"))
        assert tags[net.TARGET_HOST] == tags[net.SERVER_ADDRESS]

    def test_returns_empty_when_get_default_node_returns_none(self):
        instance = mock.MagicMock()
        instance.get_default_node.return_value = None
        assert _extract_cluster_node_conn_tags(instance) == {}

    def test_returns_empty_when_get_default_node_raises(self):
        instance = mock.MagicMock()
        instance.get_default_node.side_effect = RuntimeError("boom")
        assert _extract_cluster_node_conn_tags(instance) == {}

    def test_returns_empty_when_node_host_raises(self):
        node = mock.MagicMock()
        type(node).host = mock.PropertyMock(side_effect=AttributeError)
        instance = mock.MagicMock()
        instance.get_default_node.return_value = node
        assert _extract_cluster_node_conn_tags(instance) == {}

    def test_returns_exactly_three_keys(self):
        tags = _extract_cluster_node_conn_tags(self._make_instance())
        assert len(tags) == 3

    def test_no_db_tag_in_cluster_tags(self):
        from ddtrace.ext import redis as redisx

        tags = _extract_cluster_node_conn_tags(self._make_instance())
        assert redisx.DB not in tags


# ---------------------------------------------------------------------------
# _build_tags — routing logic
# ---------------------------------------------------------------------------


class TestBuildTagsRouting:
    """Verify _build_tags picks the right extraction path depending on the instance type."""

    def _make_pool_instance(self, host="localhost", port=6379):
        pool = mock.MagicMock()
        pool.connection_kwargs = {"host": host, "port": port, "db": 0}
        instance = mock.MagicMock(spec=["connection_pool"])
        instance.connection_pool = pool
        return instance

    def _make_cluster_instance(self, host="cluster.local", port=7000):
        node = mock.MagicMock()
        node.host = host
        node.port = port
        instance = mock.MagicMock(spec=["nodes_manager", "get_default_node"])
        instance.get_default_node.return_value = node
        return instance

    def _make_bare_instance(self):
        return mock.MagicMock(spec=[])

    def test_uses_connection_pool_when_present(self):
        instance = self._make_pool_instance(host="pool-host", port=6380)
        tags = _build_tags(None, instance, "redis")
        assert tags[net.TARGET_HOST] == "pool-host"

    def test_uses_nodes_manager_when_no_connection_pool(self):
        instance = self._make_cluster_instance(host="cluster-host", port=7001)
        tags = _build_tags(None, instance, "redis")
        assert tags[net.TARGET_HOST] == "cluster-host"

    def test_connection_pool_takes_precedence_over_nodes_manager(self):
        """If somehow both exist, connection_pool wins (existing behaviour)."""
        node = mock.MagicMock()
        node.host = "should-not-appear"
        node.port = 9999
        pool = mock.MagicMock()
        pool.connection_kwargs = {"host": "pool-wins", "port": 6379, "db": 0}
        instance = mock.MagicMock(spec=["connection_pool", "nodes_manager", "get_default_node"])
        instance.connection_pool = pool
        instance.get_default_node.return_value = node
        tags = _build_tags(None, instance, "redis")
        assert tags[net.TARGET_HOST] == "pool-wins"

    def test_no_connection_tags_when_neither_attribute_exists(self):
        instance = self._make_bare_instance()
        tags = _build_tags(None, instance, "redis")
        assert net.TARGET_HOST not in tags
        assert net.TARGET_PORT not in tags

    def test_always_sets_span_kind(self):
        from ddtrace.constants import SPAN_KIND
        from ddtrace.ext import SpanKind

        instance = self._make_bare_instance()
        tags = _build_tags(None, instance, "redis")
        assert tags[SPAN_KIND] == SpanKind.CLIENT

    def test_always_sets_component(self):
        from ddtrace.internal.constants import COMPONENT

        instance = self._make_bare_instance()
        tags = _build_tags(None, instance, "mycomponent")
        assert tags[COMPONENT] == "mycomponent"

    def test_always_sets_db_system(self):
        from ddtrace.ext import db

        instance = self._make_bare_instance()
        tags = _build_tags(None, instance, "redis")
        assert tags[db.SYSTEM] == "redis"

    def test_query_sets_raw_command_tag(self):
        instance = self._make_bare_instance()
        tags = _build_tags("GET mykey", instance, "redis")
        from ddtrace.ext import redis as redisx

        assert tags.get(redisx.RAWCMD) == "GET mykey" or any("raw_command" in str(k) for k in tags)

    def test_none_query_omits_raw_command_tag(self):
        from ddtrace.ext import redis as redisx

        instance = self._make_bare_instance()
        tags = _build_tags(None, instance, "redis")
        assert redisx.RAWCMD not in tags

    def test_cluster_tags_include_server_address(self):
        instance = self._make_cluster_instance(host="cluster.local", port=7000)
        tags = _build_tags(None, instance, "redis")
        assert tags[net.SERVER_ADDRESS] == "cluster.local"

    def test_cluster_tags_port_is_numeric(self):
        instance = self._make_cluster_instance(port=7003)
        tags = _build_tags(None, instance, "redis")
        assert tags[net.TARGET_PORT] == 7003
        assert isinstance(tags[net.TARGET_PORT], int)

    def test_cluster_graceful_when_get_default_node_returns_none(self):
        instance = mock.MagicMock(spec=["nodes_manager", "get_default_node"])
        instance.get_default_node.return_value = None
        tags = _build_tags(None, instance, "redis")
        assert net.TARGET_HOST not in tags

    def test_cluster_graceful_when_get_default_node_raises(self):
        instance = mock.MagicMock(spec=["nodes_manager", "get_default_node"])
        instance.get_default_node.side_effect = Exception("connection refused")
        tags = _build_tags(None, instance, "redis")
        assert net.TARGET_HOST not in tags
