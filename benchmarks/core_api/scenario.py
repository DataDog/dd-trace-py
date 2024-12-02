import bm

from ddtrace.internal import core


CUSTOM_EVENT_NAME = "CoreAPIScenario.event"

if not hasattr(core, "dispatch_with_results"):
    core.dispatch_with_results = core.dispatch


class CoreAPIScenario(bm.Scenario):
    listeners: int
    all_listeners: int
    set_item_count: int
    get_item_exists: bool

    def run(self):
        # Activate a number of no-op listeners for known events
        for _ in range(self.listeners):

            def listener(*_):
                pass

            core.on(CUSTOM_EVENT_NAME, listener)
            core.on("context.started.with_data", listener)
            core.on("context.ended.with_data", listener)

        for _ in range(self.all_listeners):
            if hasattr(core, "on_all"):

                def all_listener(event_id, args):
                    pass

                core.on_all(all_listener)
            else:

                def listener(*_):
                    pass

                # If we don't support "core.on_all", just double up the registered listeners to try
                # and make the comparison semi-equal
                core.on(self.CUSTOM_EVENT_NAME, listener)
                core.on("context.started.with_data", listener)
                core.on("context.ended.with_data", listener)

        if self.get_item_exists:
            core.set_item("key", "value")

        def core_dispatch(loops):
            """Measure the cost to dispatch an event on the hub"""
            for _ in range(loops):
                core.dispatch(self.CUSTOM_EVENT_NAME, (5, 6, 7, 8))

        def core_dispatch_with_results(loops):
            """Measure the cost to dispatch an event on the hub"""
            for _ in range(loops):
                core.dispatch_with_results(self.CUSTOM_EVENT_NAME, (5, 6, 7, 8))

        def context_with_data(loops):
            """Measure the cost of creating and ending a new context"""
            for _ in range(loops):
                with core.context_with_data("with_data"):
                    pass

        def set_item(loops):
            """Measure the overhead of setting keys on a context"""
            for i in range(loops):
                with core.context_with_data("with_data") as ctx:
                    key = f"key-{i}"
                    for _ in range(self.set_item_count):
                        ctx.set_item(key, "value")

        def get_item(loops):
            """Measure the cost to fetch an item from the root context"""
            for _ in range(loops):
                core.get_item("key")

        if "core_dispatch_with_results" in self.scenario_name:
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
