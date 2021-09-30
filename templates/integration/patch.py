from ddtrace import config


config._add(
    "foo",
    {
        "distributed_tracing": True,
        "_default_service": "foo",
    },
)


def patch():
    # Do monkey patching here
    pass


def unpatch():
    # Undo the monkey patching that patch() did here
    pass
