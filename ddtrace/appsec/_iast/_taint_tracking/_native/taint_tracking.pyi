from typing import Optional
from typing import Sequence
from typing import Union

class OriginType:
    EMPTY: OriginType
    PARAMETER: OriginType
    PARAMETER_NAME: OriginType
    HEADER: OriginType
    HEADER_NAME: OriginType
    PATH: OriginType
    BODY: OriginType
    QUERY: OriginType
    PATH_PARAMETER: OriginType
    COOKIE: OriginType
    COOKIE_NAME: OriginType
    GRPC_BODY: OriginType
    name: str
    value: int

    def __init__(self, value: int = ...) -> None: ...

class TagMappingMode:
    Normal: TagMappingMode
    Mapper: TagMappingMode
    Mapper_Replace: TagMappingMode

class VulnerabilityType:
    CODE_INJECTION: VulnerabilityType
    COMMAND_INJECTION: VulnerabilityType
    HEADER_INJECTION: VulnerabilityType
    UNVALIDATED_REDIRECT: VulnerabilityType
    INSECURE_COOKIE: VulnerabilityType
    NO_HTTPONLY_COOKIE: VulnerabilityType
    NO_SAMESITE_COOKIE: VulnerabilityType
    PATH_TRAVERSAL: VulnerabilityType
    SQL_INJECTION: VulnerabilityType
    SSRF: VulnerabilityType
    STACKTRACE_LEAK: VulnerabilityType
    UNTRUSTED_SERIALIZATION: VulnerabilityType
    WEAK_CIPHER: VulnerabilityType
    WEAK_HASH: VulnerabilityType
    WEAK_RANDOMNESS: VulnerabilityType
    XSS: VulnerabilityType
    name: str
    value: int

class Source:
    name: str
    value: str
    origin: OriginType

    def __init__(self, name: str = ..., value: str = ..., origin: OriginType = ...) -> None: ...
    def to_string(self) -> str: ...

class taint_range:
    start: int
    length: int
    source: Source
    secure_marks: int

    def get_hash(self) -> int: ...
    def add_secure_mark(self, vulnerability_type: Union[int, VulnerabilityType]) -> None: ...
    def has_secure_mark(self, vulnerability_type: Union[int, VulnerabilityType]) -> bool: ...
    def has_origin(self, origin: OriginType) -> bool: ...
    def __init__(self, start: int, length: int, source: Source, secure_marks: Optional[object] = ...) -> None: ...

TaintRange_ = taint_range

def copy_and_shift_ranges_from_strings(
    str_1: object,
    str_2: object,
    offset: int,
    new_length: int = ...,
    context_id: Optional[int] = ...,
) -> None: ...
def copy_ranges_from_strings(str_1: object, str_2: object, context_id: Optional[int] = ...) -> None: ...
def get_range_by_hash(range_hash: int, taint_ranges: Sequence[taint_range]) -> Optional[taint_range]: ...
def get_ranges(string_input: object, context_id: Optional[int] = ...) -> list[taint_range]: ...
def is_tainted(candidate: object) -> bool: ...
def origin_to_str(origin: OriginType) -> str: ...
def set_ranges(candidate: object, ranges: Sequence[taint_range], contextid: int) -> bool: ...
def shift_taint_range(source_taint_range: taint_range, offset: int, new_length: int = ...) -> taint_range: ...
def shift_taint_ranges(ranges: Sequence[taint_range], offset: int, new_length: int = ...) -> list[taint_range]: ...
def str_to_origin(origin: str) -> OriginType: ...
