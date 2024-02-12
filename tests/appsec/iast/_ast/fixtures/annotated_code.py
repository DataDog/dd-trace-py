#!/usr/bin/env python3


from typing import Any
from typing import Dict
from typing import Optional
from typing import Sequence
from typing import TypeVar  # noqa:F401


_T_co = TypeVar("_T_co", bound=Any, covariant=True)


class Client(Sequence[_T_co]):
    def init(self, *myargs: Optional[Sequence[Any]], **mykwargs: Optional[Dict[str, Any]]) -> None:
        super().__init__(*myargs, **mykwargs)
