
class Subscription(object):
    def run(self, store, new_addresses):
        raise NotImplementedError("Please implement a proper subscription")


class Gateway(object):
    def __init__(self):
        self._shortcuts = {}
        self._needed_addresses = set()

    def is_needed(self, address):
        return address in self._needed_addresses

    def subscribe(self, addresses, subscription):
        # type: (str, Subscription) -> None
        for address in addresses:
            self._needed_addresses.add(address)
            if address not in self._shortcuts:
                self._shortcuts[address] = []
            self._shortcuts[address].append(subscription)

    def propagate(self, store, data):
        store["addresses"].update(data)
        new_keys = data.keys()
        todo = set()
        for key in new_keys:
            if key not in self._shortcuts:
                continue
            for subscription in self._shortcuts[key]:
                if subscription in todo:
                    continue
                todo.add(subscription)
        results = []
        for sub in todo:
            sub.run(store, new_keys)
        return results
