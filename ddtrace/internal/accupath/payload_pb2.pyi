from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar, Iterable, Mapping, Optional, Union

BREAKDOWN: Source
CLIENT: Source
DESCRIPTOR: _descriptor.FileDescriptor
GRPC: EdgeType
HTTP: EdgeType
UNKNOWN: EdgeType

class DataPathAPIPayload(_message.Message):
    __slots__ = ["node", "paths"]
    NODE_FIELD_NUMBER: ClassVar[int]
    PATHS_FIELD_NUMBER: ClassVar[int]
    node: NodeID
    paths: _containers.RepeatedCompositeFieldContainer[Paths]
    def __init__(self, node: Optional[Union[NodeID, Mapping]] = ..., paths: Optional[Iterable[Union[Paths, Mapping]]] = ...) -> None: ...

class DataPathPayload(_message.Message):
    __slots__ = ["datapath", "org_id", "source"]
    DATAPATH_FIELD_NUMBER: ClassVar[int]
    ORG_ID_FIELD_NUMBER: ClassVar[int]
    SOURCE_FIELD_NUMBER: ClassVar[int]
    datapath: DataPathAPIPayload
    org_id: int
    source: Source
    def __init__(self, org_id: Optional[int] = ..., datapath: Optional[Union[DataPathAPIPayload, Mapping]] = ..., source: Optional[Union[Source, str]] = ...) -> None: ...

class EdgeID(_message.Message):
    __slots__ = ["name", "type"]
    NAME_FIELD_NUMBER: ClassVar[int]
    TYPE_FIELD_NUMBER: ClassVar[int]
    name: str
    type: EdgeType
    def __init__(self, type: Optional[Union[EdgeType, str]] = ..., name: Optional[str] = ...) -> None: ...

class Latencies(_message.Message):
    __slots__ = ["error_latency", "ok_latency"]
    ERROR_LATENCY_FIELD_NUMBER: ClassVar[int]
    OK_LATENCY_FIELD_NUMBER: ClassVar[int]
    error_latency: bytes
    ok_latency: bytes
    def __init__(self, ok_latency: Optional[bytes] = ..., error_latency: Optional[bytes] = ...) -> None: ...

class NodeID(_message.Message):
    __slots__ = ["env", "host", "primary_tags", "service"]
    ENV_FIELD_NUMBER: ClassVar[int]
    HOST_FIELD_NUMBER: ClassVar[int]
    PRIMARY_TAGS_FIELD_NUMBER: ClassVar[int]
    SERVICE_FIELD_NUMBER: ClassVar[int]
    env: str
    host: str
    primary_tags: _containers.RepeatedScalarFieldContainer[str]
    service: str
    def __init__(self, service: Optional[str] = ..., env: Optional[str] = ..., host: Optional[str] = ..., primary_tags: Optional[Iterable[str]] = ...) -> None: ...

class Paths(_message.Message):
    __slots__ = ["duration", "start", "stats"]
    DURATION_FIELD_NUMBER: ClassVar[int]
    START_FIELD_NUMBER: ClassVar[int]
    STATS_FIELD_NUMBER: ClassVar[int]
    duration: int
    start: int
    stats: _containers.RepeatedCompositeFieldContainer[PathwayStats]
    def __init__(self, start: Optional[int] = ..., duration: Optional[int] = ..., stats: Optional[Iterable[Union[PathwayStats, Mapping]]] = ...) -> None: ...

class PathwayInfo(_message.Message):
    __slots__ = ["node_hash", "parent_hash", "root_hash"]
    NODE_HASH_FIELD_NUMBER: ClassVar[int]
    PARENT_HASH_FIELD_NUMBER: ClassVar[int]
    ROOT_HASH_FIELD_NUMBER: ClassVar[int]
    node_hash: int
    parent_hash: int
    root_hash: int
    def __init__(self, node_hash: Optional[int] = ..., parent_hash: Optional[int] = ..., root_hash: Optional[int] = ...) -> None: ...

class PathwayStats(_message.Message):
    __slots__ = ["edge", "info", "latencies"]
    EDGE_FIELD_NUMBER: ClassVar[int]
    INFO_FIELD_NUMBER: ClassVar[int]
    LATENCIES_FIELD_NUMBER: ClassVar[int]
    edge: EdgeID
    info: PathwayInfo
    latencies: Latencies
    def __init__(self, edge: Optional[Union[EdgeID, Mapping]] = ..., info: Optional[Union[PathwayInfo, Mapping]] = ..., latencies: Optional[Union[Latencies, Mapping]] = ...) -> None: ...

class StoredNodeInfo(_message.Message):
    __slots__ = ["env", "service"]
    ENV_FIELD_NUMBER: ClassVar[int]
    SERVICE_FIELD_NUMBER: ClassVar[int]
    env: str
    service: str
    def __init__(self, service: Optional[str] = ..., env: Optional[str] = ...) -> None: ...

class UnresolvedDataPath(_message.Message):
    __slots__ = ["api_key", "datapath", "org_id", "source"]
    API_KEY_FIELD_NUMBER: ClassVar[int]
    DATAPATH_FIELD_NUMBER: ClassVar[int]
    ORG_ID_FIELD_NUMBER: ClassVar[int]
    SOURCE_FIELD_NUMBER: ClassVar[int]
    api_key: str
    datapath: DataPathAPIPayload
    org_id: int
    source: Source
    def __init__(self, api_key: Optional[str] = ..., datapath: Optional[Union[DataPathAPIPayload, Mapping]] = ..., org_id: Optional[int] = ..., source: Optional[Union[Source, str]] = ...) -> None: ...

class EdgeType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = []

class Source(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = []
