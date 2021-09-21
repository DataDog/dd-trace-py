import abc

import six


class Propagator(six.with_metaclass(abc.ABCMeta)):
    @staticmethod
    @abc.abstractmethod
    def inject(span_context, carrier):
        pass

    @staticmethod
    @abc.abstractmethod
    def extract(carrier):
        pass
