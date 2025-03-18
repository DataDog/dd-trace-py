from abc import ABC
from abc import abstractmethod
from typing import Any
from typing import Callable
from typing import Dict
from typing import Generic
from typing import List
from typing import Optional
from typing import Type
from typing import TypeVar
from typing import Union

from ddtrace.appsec._constants import DEFAULT
from ddtrace.appsec._utils import _observator
from ddtrace.internal.logger import get_logger


LOGGER = get_logger(__name__)


T = TypeVar("T")


DDWafRulesType = Union[None, int, str, List[Any], Dict[str, Any]]


class DDWaf_result:
    __slots__ = ["return_code", "data", "actions", "runtime", "total_runtime", "timeout", "truncation", "derivatives"]

    def __init__(
        self,
        return_code: int,
        data: List[Dict[str, Any]],
        actions: Dict[str, Any],
        runtime: float,
        total_runtime: float,
        timeout: bool,
        truncation: _observator,
        derivatives: Dict[str, Any],
    ):
        self.return_code = return_code
        self.data = data
        self.actions = actions
        self.runtime = runtime
        self.total_runtime = total_runtime
        self.timeout = timeout
        self.truncation = truncation
        self.derivatives = derivatives

    def __repr__(self):
        return (
            f"DDWaf_result(return_code: {self.return_code} data: {self.data},"
            f" actions: {self.actions}, runtime: {self.runtime},"
            f" total_runtime: {self.total_runtime}, timeout: {self.timeout},"
            f" truncation: {self.truncation}, derivatives: {self.derivatives})"
        )


class DDWaf_info:
    __slots__ = ["loaded", "failed", "errors", "version"]

    def __init__(self, loaded: int, failed: int, errors: str, version: str):
        self.loaded = loaded
        self.failed = failed
        self.errors = errors
        self.version = version

    def __repr__(self):
        return "{loaded: %d, failed: %d, errors: %s, version: %s}" % (
            self.loaded,
            self.failed,
            self.errors,
            self.version,
        )


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
    def update_rules(self, new_rules: Dict[str, Any]) -> bool:
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
