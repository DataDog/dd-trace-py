from typing import Optional
from typing import Sequence
from typing import TypeVar
from typing import Union
from typing import overload

from ddtrace.appsec._iast._typing import TextType

from .taint_tracking import TagMappingMode
from .taint_tracking import TaintRange_

_NativeTextTypeT = TypeVar("_NativeTextTypeT", bound=TextType)

def _convert_escaped_text_to_tainted_text(
    taint_escaped_text: _NativeTextTypeT, ranges_orig: Sequence[TaintRange_]
) -> _NativeTextTypeT: ...
def are_all_text_all_ranges(
    candidate_text: object, parameter_list: Union[list[object], tuple[object, ...]]
) -> tuple[list[TaintRange_], list[TaintRange_]]: ...
def as_formatted_evidence(
    text: _NativeTextTypeT,
    text_ranges: Optional[Sequence[TaintRange_]] = ...,
    tag_mapping_function: Optional[TagMappingMode] = ...,
    new_ranges: Optional[dict[TaintRange_, TaintRange_]] = ...,
) -> _NativeTextTypeT: ...
def common_replace(
    string_method: str, candidate_text: _NativeTextTypeT, *args: object, **kwargs: object
) -> _NativeTextTypeT: ...
def parse_params(
    position: int, keyword_name: str, default_value: object, *args: object, **kwargs: object
) -> object: ...
@overload
def set_ranges_on_splitted(
    source_str: str,
    source_ranges: Sequence[TaintRange_],
    split_result: list[str],
    include_separator: bool,
    context_id: int,
) -> bool: ...
@overload
def set_ranges_on_splitted(
    source_str: bytes,
    source_ranges: Sequence[TaintRange_],
    split_result: list[bytes],
    include_separator: bool,
    context_id: int,
) -> bool: ...
@overload
def set_ranges_on_splitted(
    source_str: bytearray,
    source_ranges: Sequence[TaintRange_],
    split_result: list[bytearray],
    include_separator: bool,
    context_id: int,
) -> bool: ...
