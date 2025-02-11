from ddtrace import config
from ddtrace import tracer


config.env = "dev"
config.service = "error-bug-bash"
config.version = "0.1"


@tracer.wrap()
def test():
    try:
        raise ValueError("this is an error")
    except ValueError:
        print("error caught")


test()
