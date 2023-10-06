from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar, Iterable, Mapping, Optional, Union

DESCRIPTOR: _descriptor.FileDescriptor
GRPC: EdgeType
HTTP: EdgeType
UNKNOWN: EdgeType

class DataPathAPIPayload(_message.Message):
    __slots__ = ["node", "paths"]
    NODE_FIELD_NUMBER: ClassVar[int]
    PATHS_FIELD_NUMBER: ClassVar[int]
    node: NodeID
    paths: Paths
    def __init__(self, node: Optional[Union[NodeID, Mapping]] = ..., paths: Optional[Union[Paths, Mapping]] = ...) -> None: ...

class DataPathPayload(_message.Message):
    __slots__ = ["datapath", "org_id"]
    DATAPATH_FIELD_NUMBER: ClassVar[int]
    ORG_ID_FIELD_NUMBER: ClassVar[int]
    datapath: DataPathAPIPayload
    org_id: int
    def __init__(self, org_id: Optional[int] = ..., datapath: Optional[Union[DataPathAPIPayload, Mapping]] = ...) -> None: ...

class EdgeID(_message.Message):
    __slots__ = ["name", "type"]
    NAME_FIELD_NUMBER: ClassVar[int]
    TYPE_FIELD_NUMBER: ClassVar[int]
    name: str
    type: EdgeType
    def __init__(self, type: Optional[Union[EdgeType, str]] = ..., name: Optional[str] = ...) -> None: ...

class NodeID(_message.Message):
    __slots__ = ["env", "host", "service"]
    ENV_FIELD_NUMBER: ClassVar[int]
    HOST_FIELD_NUMBER: ClassVar[int]
    SERVICE_FIELD_NUMBER: ClassVar[int]
    env: str
    host: str
    service: str
    def __init__(self, service: Optional[str] = ..., env: Optional[str] = ..., host: Optional[str] = ...) -> None: ...

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
    __slots__ = ["downstream_pathway_hash", "node_hash", "root_service_hash", "upstream_pathway_hash"]
    DOWNSTREAM_PATHWAY_HASH_FIELD_NUMBER: ClassVar[int]
    NODE_HASH_FIELD_NUMBER: ClassVar[int]
    ROOT_SERVICE_HASH_FIELD_NUMBER: ClassVar[int]
    UPSTREAM_PATHWAY_HASH_FIELD_NUMBER: ClassVar[int]
    downstream_pathway_hash: int
    node_hash: int
    root_service_hash: int
    upstream_pathway_hash: int
    def __init__(self, root_service_hash: Optional[int] = ..., node_hash: Optional[int] = ..., upstream_pathway_hash: Optional[int] = ..., downstream_pathway_hash: Optional[int] = ...) -> None: ...

class PathwayStats(_message.Message):
    __slots__ = ["edge", "info", "request_latency", "response_latency"]
    EDGE_FIELD_NUMBER: ClassVar[int]
    INFO_FIELD_NUMBER: ClassVar[int]
    REQUEST_LATENCY_FIELD_NUMBER: ClassVar[int]
    RESPONSE_LATENCY_FIELD_NUMBER: ClassVar[int]
    edge: EdgeID
    info: PathwayInfo
    request_latency: bytes
    response_latency: bytes
    def __init__(self, edge: Optional[Union[EdgeID, Mapping]] = ..., info: Optional[Union[PathwayInfo, Mapping]] = ..., request_latency: Optional[bytes] = ..., response_latency: Optional[bytes] = ...) -> None: ...

class UnresolvedDataPath(_message.Message):
    __slots__ = ["api_key", "datapath"]
    API_KEY_FIELD_NUMBER: ClassVar[int]
    DATAPATH_FIELD_NUMBER: ClassVar[int]
    api_key: str
    datapath: DataPathAPIPayload
    def __init__(self, api_key: Optional[str] = ..., datapath: Optional[Union[DataPathAPIPayload, Mapping]] = ...) -> None: ...

class EdgeType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = []
