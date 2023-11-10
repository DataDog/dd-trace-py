from ddtrace import config
from ddtrace.internal.schema import schematize_service_name


config._add(
    "foo",
    {
        "distributed_tracing": True,
        "_default_service": schematize_service_name("foo"),
    },
)


def get_version():
    # get the package distribution version here
    pass


def patch():
    # Do monkey patching here
    pass


def unpatch():
    # Undo the monkey patching that patch() did here
    pass
