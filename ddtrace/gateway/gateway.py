import attr


@attr.s(eq=False)
class Gateway(object):

    _addresses_to_keep = attr.ib(type=set, factory=set)

    def clear(self):
        self._addresses_to_keep.clear()

    @property
    def needed_address_count(self):
        # type: () -> int
        return len(self._addresses_to_keep)

    def is_needed(self, address):
        # type: (str) -> bool
        return address in self._addresses_to_keep

    def mark_needed(self, address):
        # type: (str) -> None
        self._addresses_to_keep.add(address)

    def propagate(self, store, data):
        # type: (dict, dict) -> None
        for key in data.keys():
            if key in self._addresses_to_keep:
                store.kept_addresses[key] = data[key]
