from ddtrace.internal.compat import is_wrapted


if __name__ == "__main__":
    # have to import dramatiq in order to have the post-import hooks run
    import dramatiq
    from dramatiq.brokers.stub import StubBroker

    broker = StubBroker()
    dramatiq.set_broker(broker)

    @dramatiq.actor()
    def add_numbers(a: int, b: int):
        return a + b

    # now dramatiq should be patched
    actor = broker.get_actor("add_numbers")
    assert is_wrapted(dramatiq.Actor.send_with_options)
    print("Test success")
