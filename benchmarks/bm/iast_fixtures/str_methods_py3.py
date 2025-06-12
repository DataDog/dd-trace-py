# Python 3 only functions (syntax errors on Python 2)
import re
from typing import Any
from typing import List
from typing import Optional


COMPILED_RE = re.compile(
    r"(?<!\.)(__import__|a(?:bs|iter|ll|ny)|b(?:in|ool|reakpoint|yte(?:array|s))|c(?:allable|hr|lassmethod|"
    r"omp(?:ile|lex))|d(?:elattr|i(?:ct|r|vmod))|e(?:numerate|val)|f(?:ilter|(?:loa|orma|rozense)t)|"
    r"g(?:etattr|lobals)|h(?:as(?:attr|h)|ex)|i(?:d|n(?:(?:(?:pu)?)t)|s(?:instance|subclass)|ter)|"
    r"l(?:en|ist|ocals)|m(?:a(?:[px])|emoryview|in)|next|o(?:bject|ct|pen|rd)|p(?:ow|r(?:int|"  # codespell:ignore
    r"operty))|r(?:ange|e(?:pr|versed)|ound)|s(?:et(?:(?:attr)?)|lice|orted|t(?:aticmethod|r)|"
    r"u(?:m|per))|t(?:(?:upl|yp)e)|vars|zip)\b",
    re.MULTILINE,
)


def do_fstring(a: str) -> str:
    return f"{a}"


def do_fstring_operations(a, b):
    return f"{a} + {b} = {a + b}"


def do_zero_padding_fstring(a: int, spec: str = "05d") -> str:
    return f"{a:{spec}}"


def do_fmt_value(a: str, spec: str = "<8s") -> str:
    return f"{a:{spec}}bar"


def do_repr_fstring(a: Any) -> str:
    return f"{a!r}"


def do_repr_fstring_twice(a: Any) -> str:
    return f"{a!r} {a!r}"


def do_repr_fstring_twice_different_objects(a: Any, b: Any) -> str:
    return f"{a!r} {b!r}"


def do_repr_fstring_with_format(a: Any) -> str:
    return f"{a!r:10}"


def do_repr_fstring_with_format_twice(a: Any) -> str:
    return f"{a!r:10} {a!r:11}"


def do_repr_fstring_with_expression1() -> str:
    return f"Hello world, {False or True}!"


def do_repr_fstring_with_expression2() -> str:
    return f"Hello world, {'True' * 1}!"


def do_repr_fstring_with_expression3() -> str:
    return f"Hello world, {'true'.capitalize()}!"


def do_repr_fstring_with_expression4() -> str:
    import math

    return f"Hello world, {math.sin(5.5) <= 0}!"


def do_repr_fstring_with_expression5() -> str:
    return f"Hello world, {str([False, False, True, False][400 % 199]).lower().capitalize()}!"


class Resolver404(Exception):
    pass


class ResolverMatch:
    def __init__(self, *args: List[Any], **kwargs: List[Any]) -> None:
        pass


COUNTER = 0


class URLPattern:
    default_kwargs = {}
    pattern = None
    url_patterns = None
    app_name = None
    namespace = None

    def __init__(self, pattern: Optional["URLPattern"] = None) -> None:
        self.pattern = pattern
        self.url_patterns = [self.pattern]

    def _join_route(self, current_route: str, sub_match_route: str) -> str:
        return "".join([current_route, sub_match_route])

    def match(self, path: str):
        global COUNTER
        COUNTER = COUNTER + 1
        if COUNTER > 4:
            return False
        return path, path, path

    def resolve(self, path: str) -> Optional[ResolverMatch]:
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


def do_match_group(text: str) -> str:
    #
    #  TODO(avara1986): This kind of assignation doesn't work with AST patching
    #    my_re_match_function = COMPILED_RE.match
    result = COMPILED_RE.match(text, 0)
    return result.group()
