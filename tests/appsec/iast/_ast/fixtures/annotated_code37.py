#!/usr/bin/env python3


from typing import Any
from typing import Dict
from typing import Optional
from typing import Sequence
from typing import Tuple
from typing import TypeVar  # noqa:F401


_T_co = TypeVar("_T_co", bound=Any, covariant=True)

foo: Optional[int] = 42

MySequenceType = Sequence[int]

# bar = [1, 2, 3, 4][0]


def bar(arg: Dict[str, Sequence[Any]]) -> Tuple[str, ...]:
    return tuple(arg.keys())


# function with keyword-only arguments
def greet(name: Optional[str] = "World", endline: Optional[str] = "!", *, greeting: Optional[str] = "Hello"):
    return greeting + ", " + name + endline


class Client(Sequence[_T_co]):
    my_attr: Optional[str]

    def init(self, *myargs: Optional[Sequence[Any]], **mykwargs: Optional[Dict[str, Any]]) -> Optional[None]:
        super().__init__(*myargs, **mykwargs)
