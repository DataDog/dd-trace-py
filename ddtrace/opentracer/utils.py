import ddtrace


def get_context_provider_for_scope_manager(scope_manager):
    """Returns the context_provider to use with a given scope_manager."""

    scope_manager_type = type(scope_manager).__name__

    # avoid having to import scope managers which may not be compatible
    # with the version of python being used
    if scope_manager_type == "AsyncioScopeManager":
        dd_context_provider = ddtrace.contrib.asyncio.context_provider
    elif scope_manager_type == "GeventScopeManager":
        dd_context_provider = ddtrace.contrib.gevent.context_provider
    else:
        dd_context_provider = ddtrace.provider.DefaultContextProvider()

    return dd_context_provider
