from typing import Dict
from typing import Text
from typing import Union

from ddtrace.internal.compat import NumericType
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

_TagNameType = Union[Text, bytes]
_MetaDictType = Dict[_TagNameType, Text]
_MetricDictType = Dict[_TagNameType, NumericType]
