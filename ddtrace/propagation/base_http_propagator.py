import abc
from typing import Dict

import six

from ..context import Context


class BaseHTTPPropagator(six.with_metaclass(abc.ABCMeta)):
    @staticmethod
    @abc.abstractmethod
    def inject(span_context, headers):
        # type: (Context, Dict[str, str]) -> None
        pass

    @staticmethod
    @abc.abstractmethod
    def extract(headers):
        # type: (Dict[str,str]) -> Context
        pass
