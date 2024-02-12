import json
import re
import string
import sys
from typing import TYPE_CHECKING  # noqa:F401

import attr

from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config


if TYPE_CHECKING:
    from typing import Any  # noqa:F401
    from typing import List  # noqa:F401
    from typing import Set  # noqa:F401
    from typing import Tuple  # noqa:F401


def _is_python_version_supported():  # type: () -> bool
    # IAST supports Python versions 3.6 to 3.12
    return (3, 6, 0) <= sys.version_info < (3, 13, 0)


def _is_iast_enabled():
    if not asm_config._iast_enabled:
        return False

    if not _is_python_version_supported():
        log = get_logger(__name__)
        log.info("IAST is not compatible with the current Python version")
        return False

    return True


# Used to cache the compiled regular expression
_SOURCE_NAME_SCRUB = None
_SOURCE_VALUE_SCRUB = None


def _has_to_scrub(s):  # type: (str) -> bool
    global _SOURCE_NAME_SCRUB
    global _SOURCE_VALUE_SCRUB

    if _SOURCE_NAME_SCRUB is None:
        _SOURCE_NAME_SCRUB = re.compile(asm_config._iast_redaction_name_pattern)
        _SOURCE_VALUE_SCRUB = re.compile(asm_config._iast_redaction_value_pattern)

    return _SOURCE_NAME_SCRUB.match(s) is not None or _SOURCE_VALUE_SCRUB.match(s) is not None


_REPLACEMENTS = string.ascii_letters
_LEN_REPLACEMENTS = len(_REPLACEMENTS)


def _scrub(s, has_range=False):  # type: (str, bool) -> str
    if has_range:
        return "".join([_REPLACEMENTS[i % _LEN_REPLACEMENTS] for i in range(len(s))])
    return "*" * len(s)


def _is_evidence_value_parts(value):  # type: (Any) -> bool
    return isinstance(value, (set, list))


def _scrub_get_tokens_positions(text, tokens):
    # type: (str, Set[str]) -> List[Tuple[int, int]]
    token_positions = []

    for token in tokens:
        position = text.find(token)
        if position != -1:
            token_positions.append((position, position + len(token)))

    token_positions.sort()
    return token_positions


def _iast_report_to_str(data):
    from ._taint_tracking import OriginType
    from ._taint_tracking import origin_to_str

    class OriginTypeEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, OriginType):
                # if the obj is uuid, we simply return the value of uuid
                return origin_to_str(obj)
            return json.JSONEncoder.default(self, obj)

    return json.dumps(attr.asdict(data, filter=lambda attr, x: x is not None), cls=OriginTypeEncoder)


def _get_patched_code(module_path, module_name):  # type: (str, str) -> str
    """
    Print the patched code to stdout, for debugging purposes.
    """
    import astunparse

    from ddtrace.appsec._iast._ast.ast_patching import get_encoding
    from ddtrace.appsec._iast._ast.ast_patching import visit_ast

    with open(module_path, "r", encoding=get_encoding(module_path)) as source_file:
        source_text = source_file.read()

        new_source = visit_ast(
            source_text,
            module_path,
            module_name=module_name,
        )

        # If no modifications are done,
        # visit_ast returns None
        if not new_source:
            return ""

        new_code = astunparse.unparse(new_source)
        return new_code


if __name__ == "__main__":
    MODULE_PATH = sys.argv[1]
    MODULE_NAME = sys.argv[2]
    print(_get_patched_code(MODULE_PATH, MODULE_NAME))
