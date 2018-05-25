from abc import ABCMeta, abstractmethod


ABC = ABCMeta('ABC', (object,), {'__slots__': ()})

class Propagator(ABC):

    @abstractmethod
    def inject(self, span_context, carrier):
        pass

    @abstractmethod
    def extract(self, carrier):
        pass
