# Python 3 only functions (syntax errors on Python 2)
from typing import TYPE_CHECKING


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any
    from typing import List
    from typing import Optional
    from typing import Tuple


def do_fmt_value(a):  # type: (str) -> str
    return f"{a:<8s}bar"


def do_repr_fstring(a):  # type: (Any) -> str
    return f"{a!r}"


def do_repr_fstring_twice(a):  # type: (Any) -> str
    return f"{a!r} {a!r}"


def do_repr_fstring_twice_different_objects(a, b):  # type: (Any, Any) -> str
    return f"{a!r} {b!r}"


def do_repr_fstring_with_format(a):  # type: (Any) -> str
    return f"{a!r:10}"


class Resolver404(Exception):
    pass


class ResolverMatch:
    def __init__(self, *args, **kwargs):  # type: (List[Any], List[Any]) -> None
        pass


COUNTER = 0


class URLPattern:
    default_kwargs = {}
    pattern = None
    url_patterns = None
    app_name = None
    namespace = None

    def __init__(self, pattern=None):  # type: (URLPattern) -> None
        self.pattern = pattern
        self.url_patterns = [self.pattern]

    def _join_route(self, current_route, sub_match_route):
        return "".join([current_route, sub_match_route])

    def match(self, path):  # type: (str) -> Optional[Tuple[str, str, str], bool]
        global COUNTER
        COUNTER = COUNTER + 1
        if COUNTER > 4:
            return False
        return path, path, path

    def resolve(self, path):  # type: (str) -> Optional[ResolverMatch, None]
        path = str(path)  # path may be a reverse_lazy object
        tried = []
        match = self.pattern.match(path) if self.pattern else False
        if match:
            new_path, args, kwargs = match
            for pattern in self.url_patterns:
                try:
                    sub_match = pattern.resolve(new_path)
                except Resolver404 as e:
                    sub_tried = e.args[0].get("tried")
                    if sub_tried is not None:
                        tried.extend([pattern] + t for t in sub_tried)
                    else:
                        tried.append([pattern])
                else:
                    if sub_match:
                        # Merge captured arguments in match with submatch
                        sub_match_dict = {**kwargs, **self.default_kwargs}
                        # Update the sub_match_dict with the kwargs from the sub_match.
                        sub_match_dict.update(sub_match.kwargs)
                        # If there are *any* named groups, ignore all non-named groups.
                        # Otherwise, pass all non-named arguments as positional arguments.
                        sub_match_args = sub_match.args
                        if not sub_match_dict:
                            sub_match_args = args + sub_match.args
                        current_route = "" if isinstance(pattern, URLPattern) else str(pattern.pattern)
                        return ResolverMatch(
                            sub_match.func,
                            sub_match_args,
                            sub_match_dict,
                            sub_match.url_name,
                            [self.app_name] + sub_match.app_names,
                            [self.namespace] + sub_match.namespaces,
                            self._join_route(current_route, sub_match.route),
                        )
                    tried.append([pattern])
            raise Resolver404({"tried": tried, "path": new_path})
        raise Resolver404({"path": path})
