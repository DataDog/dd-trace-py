import Any
import abc
from abc import abstractmethod

ABC: Any

class Propagator(ABC, metaclass=abc.ABCMeta):
    @staticmethod
    @abstractmethod
    def inject(span_context: Any, carrier: Any) -> Any: ...
    @staticmethod
    @abstractmethod
    def extract(carrier: Any) -> Any: ...
