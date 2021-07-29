from abc import ABCMeta
from abc import abstractmethod


# ref: https://stackoverflow.com/a/38668373
ABC = ABCMeta("ABC", (object,), {"__slots__": ()})


class Propagator(ABC):
    @staticmethod
    @abstractmethod
    def inject(span_context, carrier):
        pass

    @staticmethod
    @abstractmethod
    def extract(carrier):
        pass
