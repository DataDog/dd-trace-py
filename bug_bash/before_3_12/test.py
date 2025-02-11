from ddtrace import tracer


@tracer.wrap()
def f():
    try:
        raise ValueError("this is an error")
    except ValueError:
        print("error caught")
