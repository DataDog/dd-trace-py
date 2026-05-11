from __future__ import annotations

from typing import Any
from typing import Protocol
from typing import Sequence
from typing import TypedDict
from typing import Union


class RedisConnectionKwargs(TypedDict, total=False):
    host: str
    port: int
    db: int
    client_name: str


class RedisConnectionPool(Protocol):
    @property
    def connection_kwargs(self) -> RedisConnectionKwargs: ...


class RedisClient(Protocol):
    @property
    def connection_pool(self) -> RedisConnectionPool: ...


class RedisClusterCommand(Protocol):
    @property
    def args(self) -> Sequence[Any]: ...


class RedisPipeline(RedisClient, Protocol):
    @property
    def command_stack(self) -> Sequence[tuple[Sequence[Any], Any]]: ...


class RedisClusterPipeline(RedisClient, Protocol):
    @property
    def command_stack(self) -> Sequence[RedisClusterCommand]: ...


RedisInstance = Union[RedisClient, RedisPipeline, RedisClusterPipeline]
