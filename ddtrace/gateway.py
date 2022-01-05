from typing import Any


class Subscription(object):
    def __init__(self, addresses):
        self.addresses = addresses

    def run(self, store):
        raise NotImplementedError("Please implement a proper subscription")


class Gateway(object):
    def __init__(self):
        self.shortcuts = {}

    def subscribe(self, subscription):
        # type: (Subscription) -> None
        for address in subscription.addresses:
            if address not in self.shortcuts:
                self.shortcuts[address] = []
            self.shortcuts[address].append(subscription)

    def propagate(self, store, data):
        # type: (dict, dict) -> map[Any]
        store.update(data)
        new_keys = data.keys()
        all_keys = set(store.keys())
        todo = set()
        for key in new_keys:
            if key not in self.shortcuts:
                continue
            for subscription in self.shortcuts[key]:
                if subscription in todo:
                    continue
                if all_keys < subscription.addresses:
                    todo.add(subscription)
        results = map(lambda sub: sub.run(store), todo)
        return results
