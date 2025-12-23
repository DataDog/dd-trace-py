import asyncio

import pytest

from ddtrace.llmobs._routing import RoutingContext
from ddtrace.llmobs._routing import _get_current_routing


class TestRoutingContext:
    def test_routing_context_sets_context(self):
        with RoutingContext(dd_api_key="test-key", dd_site="test-site"):
            routing = _get_current_routing()
            assert routing is not None
            assert routing["dd_api_key"] == "test-key"
            assert routing["dd_site"] == "test-site"

    def test_routing_context_sets_context_key_only(self):
        with RoutingContext(dd_api_key="test-key"):
            routing = _get_current_routing()
            assert routing is not None
            assert routing["dd_api_key"] == "test-key"
            assert "dd_site" not in routing

    def test_routing_context_restores_after_exit(self):
        assert _get_current_routing() is None

        with RoutingContext(dd_api_key="test-key"):
            routing = _get_current_routing()
            assert routing is not None
            assert routing["dd_api_key"] == "test-key"

        assert _get_current_routing() is None

    def test_routing_context_requires_api_key(self):
        with pytest.raises(ValueError, match="dd_api_key is required"):
            RoutingContext(dd_api_key="")

    def test_routing_context_returns_value(self):
        with RoutingContext(dd_api_key="test-key") as ctx:
            assert ctx is not None
            assert isinstance(ctx, RoutingContext)

    def test_nested_routing_contexts(self):
        with RoutingContext(dd_api_key="outer-key", dd_site="outer-site"):
            outer_routing = _get_current_routing()
            assert outer_routing["dd_api_key"] == "outer-key"
            assert outer_routing["dd_site"] == "outer-site"

            with RoutingContext(dd_api_key="inner-key", dd_site="inner-site"):
                inner_routing = _get_current_routing()
                assert inner_routing["dd_api_key"] == "inner-key"
                assert inner_routing["dd_site"] == "inner-site"

            after_inner = _get_current_routing()
            assert after_inner["dd_api_key"] == "outer-key"
            assert after_inner["dd_site"] == "outer-site"

    def test_routing_context_propagates_in_exception(self):
        try:
            with RoutingContext(dd_api_key="test-key"):
                raise ValueError("test error")
        except ValueError:
            pass

        assert _get_current_routing() is None


class TestRoutingContextAsync:
    @pytest.mark.asyncio
    async def test_async_routing_context_sets_context(self):
        async with RoutingContext(dd_api_key="async-key", dd_site="async-site"):
            routing = _get_current_routing()
            assert routing is not None
            assert routing["dd_api_key"] == "async-key"
            assert routing["dd_site"] == "async-site"

    @pytest.mark.asyncio
    async def test_async_routing_context_restores_after_exit(self):
        assert _get_current_routing() is None

        async with RoutingContext(dd_api_key="async-key"):
            routing = _get_current_routing()
            assert routing is not None

        assert _get_current_routing() is None

    @pytest.mark.asyncio
    async def test_async_routing_context_isolation(self):
        results = {}

        async def task_a():
            async with RoutingContext(dd_api_key="key-a", dd_site="site-a"):
                await asyncio.sleep(0.01)
                results["a"] = _get_current_routing()

        async def task_b():
            async with RoutingContext(dd_api_key="key-b", dd_site="site-b"):
                await asyncio.sleep(0.005)
                results["b"] = _get_current_routing()

        await asyncio.gather(task_a(), task_b())

        assert results["a"]["dd_api_key"] == "key-a"
        assert results["a"]["dd_site"] == "site-a"
        assert results["b"]["dd_api_key"] == "key-b"
        assert results["b"]["dd_site"] == "site-b"
