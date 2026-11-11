from collections.abc import Callable
from collections.abc import Generator

import bm

from ddtrace.internal import core


CUSTOM_EVENT_NAME = "CoreAPIScenario.event"

if not hasattr(core, "dispatch_with_results"):
    core.dispatch_with_results = core.dispatch  # type: ignore[assignment]


class CoreAPIScenario(bm.Scenario):  # type: ignore[misc]
    listeners: int
    set_item_count: int
    get_item_exists: bool
    listener_raises: bool  # whether registered listeners raise exceptions

    def run(self) -> Generator[Callable[[int], None], None, None]:
        if self.name in ("subscriber_setup", "subscriber_registration", "subscriber_reset_recovery"):
            from ddtrace._trace.subscribers.web_framework import WebFrameworkRequestSubscriber
            from ddtrace.internal.core import subscriber as subscriber_api

            # Release baselines predate bundles; compare the same listener activation with their direct API.
            bundle_type = getattr(subscriber_api, "SubscriberBundle", None)
            register = WebFrameworkRequestSubscriber.register
            if bundle_type is not None:
                bundle = bundle_type("ddtrace._trace.subscribers.web_framework", ("WebFrameworkRequestSubscriber",))
                register = bundle.register

            # Establish active listeners outside the timed callable.
            register()

            def subscriber_setup(loops: int) -> None:
                for _ in range(loops):
                    register()

            def subscriber_registration(loops: int) -> None:
                # Imports are warm; startup benchmarks cover cold activation.
                for _ in range(loops):
                    core.reset_listeners("context.started.web.request")
                    core.reset_listeners("context.ended.web.request")
                    if bundle_type is None:
                        register()
                    else:
                        bundle_type(bundle.module, bundle.subscribers).register()

            def subscriber_reset_recovery(loops: int) -> None:
                for _ in range(loops):
                    core.reset_listeners("context.started.web.request")
                    register()

            if self.name == "subscriber_setup":
                yield subscriber_setup
            elif self.name == "subscriber_registration":
                yield subscriber_registration
            else:
                yield subscriber_reset_recovery
            return

        for _ in range(self.listeners):
            if self.listener_raises:

                def listener(*_: object) -> None:
                    raise ValueError("benchmark listener exception")

            else:

                def listener(*_: object) -> None:
                    pass

            core.on(CUSTOM_EVENT_NAME, listener)
            core.on("context.started.with_data", listener)
            core.on("context.ended.with_data", listener)

        if self.get_item_exists:
            core.set_item("key", "value")

        def has_listeners(loops: int) -> None:
            """Measure the cost of core.has_listeners"""
            for _ in range(loops):
                core.has_listeners(CUSTOM_EVENT_NAME)

        def core_dispatch_no_args(loops: int) -> None:
            """Measure dispatch cost with an empty args tuple"""
            for _ in range(loops):
                core.dispatch(CUSTOM_EVENT_NAME, ())

        def core_dispatch(loops: int) -> None:
            """Measure the cost to dispatch an event on the hub"""
            for _ in range(loops):
                core.dispatch(CUSTOM_EVENT_NAME, (5, 6, 7, 8))

        def core_dispatch_with_results(loops: int) -> None:
            """Measure the cost to dispatch an event on the hub"""
            for _ in range(loops):
                core.dispatch_with_results(  # ast-grep-ignore: core-dispatch-with-results
                    CUSTOM_EVENT_NAME, (5, 6, 7, 8)
                )

        def context_with_data(loops: int) -> None:
            """Measure the cost of creating and ending a new context"""
            for _ in range(loops):
                with core.context_with_data("with_data"):
                    pass

        def set_item(loops: int) -> None:
            """Measure the overhead of setting keys on a context"""
            for i in range(loops):
                with core.context_with_data("with_data") as ctx:
                    key = f"key-{i}"
                    for _ in range(self.set_item_count):
                        ctx.set_item(key, "value")

        def get_item(loops: int) -> None:
            """Measure the cost to fetch an item from the root context"""
            for _ in range(loops):
                core.find_item("key")

        if "has_listeners" in self.scenario_name:
            yield has_listeners
        elif "core_dispatch_no_args" in self.scenario_name:
            yield core_dispatch_no_args
        elif "core_dispatch_with_results" in self.scenario_name:
            yield core_dispatch_with_results
        elif "core_dispatch" in self.scenario_name:
            yield core_dispatch
        elif "context_with_data" in self.scenario_name:
            yield context_with_data
        elif "set_item" in self.scenario_name:
            yield set_item
        elif "get_item" in self.scenario_name:
            yield get_item
        else:
            raise RuntimeError(f"Unknown scenario_name {self.scenario_name}")
