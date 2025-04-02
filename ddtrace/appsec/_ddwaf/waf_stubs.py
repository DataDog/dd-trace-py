from abc import ABC
from abc import abstractmethod
from typing import Any
from typing import Callable
from typing import Dict
from typing import Generic
from typing import List
from typing import Optional
from typing import Sequence
from typing import Tuple
from typing import Type
from typing import TypeVar
from typing import Union

from ddtrace.appsec._constants import DEFAULT
from ddtrace.appsec._utils import DDWaf_info
from ddtrace.appsec._utils import DDWaf_result
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig import PayloadType


LOGGER = get_logger(__name__)


T = TypeVar("T")


DDWafRulesType = Union[None, int, str, List[Any], Dict[str, Any]]


class ddwaf_handle_capsule(Generic[T]):
    def __init__(self, handle: Type[T], free_fn: Callable[[Type[T]], None]) -> None:
        self.handle = handle
        self.free_fn = free_fn

    def __del__(self):
        if self.handle:
            try:
                self.free_fn(self.handle)
            except TypeError:
                LOGGER.debug("Failed to free handle", exc_info=True)
            self.handle = None

    def __bool__(self):
        return bool(self.handle)


class ddwaf_context_capsule(Generic[T]):
    def __init__(self, ctx: Type[T], free_fn: Callable[[Type[T]], None]) -> None:
        self.ctx = ctx
        self.free_fn = free_fn

    def __del__(self):
        if self.ctx:
            try:
                self.free_fn(self.ctx)
            except TypeError:
                LOGGER.debug("Failed to free context", exc_info=True)
            self.ctx = None

    def __bool__(self):
        return bool(self.ctx)


class ddwaf_builder_capsule(Generic[T]):
    def __init__(self, builder: Type[T], free_fn: Callable[[Type[T]], None]) -> None:
        self.builder = builder
        self.free_fn = free_fn

    def __del__(self):
        if self.builder:
            try:
                self.free_fn(self.builder)
            except TypeError:
                LOGGER.debug("Failed to free builder", exc_info=True)
            self.builder = None

    def __bool__(self):
        return bool(self.builder)


class WAF(ABC):
    @property
    @abstractmethod
    def required_data(self) -> List[str]:
        pass

    @property
    @abstractmethod
    def info(self) -> DDWaf_info:
        pass

    @abstractmethod
    def update_rules(
        self, removals: Sequence[Tuple[str, str]], updates: Sequence[Tuple[str, str, PayloadType]]
    ) -> bool:
        pass

    @abstractmethod
    def _at_request_start(self) -> Optional[ddwaf_context_capsule]:
        pass

    @abstractmethod
    def _at_request_end(self):
        pass

    @abstractmethod
    def run(
        self,
        ctx: ddwaf_context_capsule,
        data: DDWafRulesType,
        ephemeral_data: DDWafRulesType = None,
        timeout_ms: float = DEFAULT.WAF_TIMEOUT,
    ) -> DDWaf_result:
        pass

    @abstractmethod
    def __init__(
        self,
        rules: Dict[str, Any],
        obfuscation_parameter_key_regexp: bytes,
        obfuscation_parameter_value_regexp: bytes,
    ):
        pass

    @property
    @abstractmethod
    def initialized(self) -> bool:
        pass
