import sys


class Either:
    def __init__(self, *possibilities):
        self.possibilities = possibilities

    def __eq__(self, other):
        if other not in self.possibilities:
            print(f"Either: Expected {other} to be in {self.possibilities}", file=sys.stderr, flush=True)
            return False
        return True
