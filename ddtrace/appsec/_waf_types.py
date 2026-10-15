"""Static annotation for WAF inputs."""

from collections.abc import Mapping
from collections.abc import Sequence
from typing import Union


WafInput = Union[None, bool, int, float, str, bytes, Sequence["WafInput"], Mapping[str, "WafInput"]]
