from ddtrace._trace.provider import DefaultContextProvider


class TracerStackContext(DefaultContextProvider):
    def __enter__(self):
        pass

    def __exit__(self, *exc):
        pass


context_provider = TracerStackContext()
