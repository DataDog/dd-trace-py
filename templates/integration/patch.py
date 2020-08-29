from ddtrace import config


config._add("foo", {
    "distributed_tracing": True
})


def patch():
    # Do monkey patching here
    pass


def unpatch():
    # Undo the monkey patching that patch() did here
    pass
