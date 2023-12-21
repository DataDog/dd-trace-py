import sys


class Mock_Alternative:
    def __init__(self, *possibilities):
        self.possibilities = possibilities

    def __eq__(self, other):
        if other not in self.possibilities:
            print(f"Mock_Alternative: Expected {other} to be in {self.possibilities}", file=sys.stderr, flush=True)
            return False
        return True
