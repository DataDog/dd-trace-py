from opentracing.scope_managers import ThreadLocalScopeManager
from opentracing.scope_managers.asyncio import AsyncioScopeManager

import ddtrace
from ddtrace.opentracer.utils import get_context_provider_for_scope_manager


class TestOpentracerUtils(object):
    def test_get_context_provider_for_scope_manager_thread(self):
        scope_manager = ThreadLocalScopeManager()
        ctx_prov = get_context_provider_for_scope_manager(scope_manager)
        assert isinstance(ctx_prov, ddtrace._trace.provider.DefaultContextProvider)

    def test_get_context_provider_for_asyncio_scope_manager(self):
        scope_manager = AsyncioScopeManager()
        ctx_prov = get_context_provider_for_scope_manager(scope_manager)
        assert isinstance(ctx_prov, ddtrace._trace.provider.DefaultContextProvider)
