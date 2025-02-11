from ddtrace import config
from ddtrace import tracer
from ddtrace.internal.error_reporting.handled_exceptions_before_3_12 import instrument_main


config.env = "dev"
config.service = "error-bug-bash"
config.version = "0.1"


@tracer.wrap()
def test():
    try:
        raise ValueError("this is an error")
    except ValueError:
        print("error caught")


instrument_main()
test()
