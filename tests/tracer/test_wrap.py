def test_generator_wrap_decorator(tracer):
    @tracer.wrap()
    def func():
        span = tracer.current_span()
        assert span is None
        yield

    result = list(func())
    assert result == [None]



def test_generator_custom_decorator(tracer):
    import functools

    def custom(name=None, service=None, resource=None, span_type=None):
        def wrap_decorator(f):
            span_name = name if name else "%s.%s" % (f.__module__, f.__name__)
            @functools.wraps(f)
            def func_wrapper(*args, **kwargs):
                with tracer.trace(span_name, service=service, resource=resource, span_type=span_type):
                    for _ in f(*args, **kwargs):
                        yield _
            return func_wrapper

        return wrap_decorator

    @custom()
    def func():
        span = tracer.current_span()
        assert span is not None
        yield
        span.set_tag("test", "1")

    result = list(func())
    assert result == [None]
    spans = tracer.pop()
    assert len(spans) == 1
    assert spans[0].get_tag("test") == "1"
