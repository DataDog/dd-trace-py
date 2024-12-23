#!/usr/bin/env python3

import ast
import codecs
import os
import re
from sys import builtin_module_names
from sys import version_info
import textwrap
from types import ModuleType
from typing import Optional
from typing import Text
from typing import Tuple

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._python_info.stdlib import _stdlib_for_python_version
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import origin
from ddtrace.internal.utils.formats import asbool

from .visitor import AstVisitor


_VISITOR = AstVisitor()

_PREFIX = IAST.PATCH_ADDED_SYMBOL_PREFIX

# Prefixes for modules where IAST patching is allowed
IAST_ALLOWLIST: Tuple[Text, ...] = ("tests.appsec.iast.",)
# How to update this "simple regex":
#  - python -m pip install trieregex
#  - go to scripts/iast/generate_import_exclusions_by_prefix.py
#  - update IAST_DENYLIST and run the script
#  - update IAST_DENYLIST_REGEX with the output
IAST_DENYLIST_REGEX = (
    r"(?:d(?:jango(?:\.(?:co(?:n(?:trib\.(?:a(?:dmin(?:\.(?:views\.(?:autocomplete\.|decorators\.|"
    r"main\.)|w(?:agtail_hooks\.|idgets\.)|a(?:ctions\.|dmin\.|pps\.)|image_formats\.|"
    r"templatetags\.|decorators\.|exceptions\.|helpers\.|options\.|checks\.|sites\.)|"
    r"docs\.(?:utils\.|views\.))|uth\.(?:c(?:ontext_processors\.|hecks\.)|m(?:anagement\.|"
    r"iddleware\.)|ba(?:se_user\.|ckends\.)|password_validation\.|a(?:dmin\.|pps\.)|"
    r"image_formats\.|wagtail_hooks\.|templatetags\.|decorators\.|validators\.|"
    r"hashers\.|signals\.))|s(?:essions\.(?:ba(?:se_session\.|ckends\.)|a(?:dmin\.|pps\.)|"
    r"image_formats\.|wagtail_hooks\.|templatetags\.|exceptions\.|middleware\.)|"
    r"taticfiles\.(?:a(?:dmin\.|pps\.)|image_formats\.|wagtail_hooks\.|templatetags\.|"
    r"finders\.|storage\.|checks\.|models\.|utils\.)|ites\.)|messages\.(?:con(?:text_processors\.|"
    r"stants\.)|a(?:p(?:ps\.|i\.)|dmin\.)|image_formats\.|wagtail_hooks\.|templatetags\.|"
    r"middleware\.|storage\.|utils\.)|contenttypes\.(?:m(?:anagement\.|odels\.)|"
    r"f(?:ields\.|orms\.)|a(?:dmin\.|pps\.)|image_formats\.|wagtail_hooks\.|templatetags\.|"
    r"checks\.|views\.)|humanize\.templatetags\.)|f\.)|"
    r"re\.(?:c(?:hecks\.(?:c(?:ompatibility\.(?:django_4_0\.)?|aches\.)|security\.(?:sessions\.|"
    r"base\.|csrf\.)?|m(?:odel_checks\.|essages\.)|t(?:ranslation\.|emplates\.)|async_checks\.|"
    r"database\.|registry\.|files\.|urls)|ache\.(?:backends\.|utils\.))|ma(?:nagement\.(?:color\.|"
    r"base\.|sql\.)|il\.)|exceptions\.|validators\.|paginator\.|signing\.))|"
    r"te(?:mplate(?:\.(?:l(?:oader(?:_tags\.|s\.|\.)|ibrary\.)|context(?:_processors\.|"  # codespell:ignore
    r"\.)|default(?:filters\.|tags\.)|e(?:xceptions\.|ngine\.)|ba(?:ckends\.|se\.)|autoreload\.|"
    r"response\.|smartif\.|utils\.)|tags\.)|st\.)|u(?:rls\.(?:con(?:verters\.|f\.)|exceptions\.|"
    r"resolvers\.|utils\.|base\.)|tils\.)|apps\.(?:registry\.|config\.)|dispatch\.dispatcher\.)|"
    r"_filters\.(?:rest_framework\.(?:filters(?:et\.|\.)|backends\.)?|co(?:n(?:stants\.|"
    r"f\.)|mpat\.)|fi(?:lters(?:et\.|\.)|elds\.)|exceptions\.|widgets\.|utils\.))|"
    r"i(?:ll\.(?:settings\.|info\.)|fflib\.)|e(?:fusedxml\.|precated\.)|d(?:sketch\.|trace\.)|"
    r"ateutil\.)|c(?:har(?:det\.(?:lang(?:t(?:urkishmodel\.|haimodel\.)|bulgarianmodel\.|"
    r"russianmodel\.|hebrewmodel\.|greekmodel\.)|e(?:uc(?:kr(?:prober\.|freq\.)|tw(?:prober\.|"
    r"freq\.)|jpprober\.)|nums\.|scsm\.)|c(?:harsetgroupprober\.|p949prober\.)|"
    r"sbc(?:harsetprober\.|sgroupprober\.)|mbcs(?:groupprober\.|sm\.)|gb2312(?:prober\.|freq\.)|"
    r"big5(?:prober\.|freq\.)|hebrewprober\.|jisfreq\.)|set_normalizer\.)|"
    r"o(?:n(?:current\.futures\.|figparser\.)|reschema\.|lorama\.)|r(?:ispy_forms\.|ypto\.)|"
    r"e(?:rtifi\.|lery\.)|chardet\.|attrs\.|lick\.|math\.|ffi\.|v2\.)|"
    r"a(?:sync(?:io\.(?:base_(?:subprocess\.|futures\.|events\.|tasks\.)|s(?:elector_events\.|"
    r"ubprocess\.|taggered\.)|t(?:r(?:ansports\.|sock\.)|hreads\.|asks\.)|co(?:routines\.|"
    r"nstants\.)|e(?:xceptions\.|vents\.)|lo(?:cks\.|g\.)|unix_events\.|protocols\.|futures\.|"
    r"runners\.|queues\.)|pg\.pgproto\.)|io(?:http\.(?:_(?:h(?:ttp_(?:parser\.|writer\.)|"
    r"elpers\.)|websocket\.)|tcp_helpers\.|log\.)|quic\.)|ttr\.(?:_(?:next_gen\.|config\.)|"
    r"filters\.|setters\.)|pi_pb2(?:_grpc\.|\.)|llauth\.|nyio\.)|"
    r"b(?:oto(?:core\.(?:vendored\.requests\.|docs\.bcdoc\.|retries\.)|3\.(?:docs\.docstring\.|"
    r"s3\.))|rotli(?:cffi\.|\.)|ackports\.|ytecode\.|linker\.)|p(?:y(?:nndescent\.|cparser\.|"
    r"dicom\.|test\.)|sycopg(?:2\.|\.)|kg_resources\.|ackaging\.|rotobuf\.|luggy\.|ip\.)|"
    r"s(?:qlalchemy\.orm\.interfaces\.|etuptools\.|klearn\.|niffio\.|anic\.|cipy\.)|"
    r"u(?:rlpatterns_reverse\.tests\.|v(?:icorn\.|loop\.)|nittest\.mock\.|map\.)|"
    r"h(?:ttp(?:tools\.|core\.|x\.)|ypothesis\.|11\.)|i(?:mportlib_metadata\.|tsdangerous\.|"
    r"nspect\.)|w(?:e(?:bsocket(?:s\.|\.)|rkzeug\.)|rapt\.)|google(?:cloudsdk\.|\.auth\.)|"
    r"e(?:xceptiongroup\.|nvier\.)|_p(?:sycopg\.|ytest\.)|f(?:reezegun\.|lask\.)|n(?:ibabel\.|"
    r"umpy\.)|opentelemetry\-api\.|typing_extensions\.|moto\.|rich\.|zipp\.)"
)
IAST_DENYLIST: Tuple[Text, ...] = ()
IAST_DENYLIST_LEN = 0


if IAST.PATCH_MODULES in os.environ:
    IAST_ALLOWLIST += tuple(os.environ[IAST.PATCH_MODULES].split(IAST.SEP_MODULES))

if IAST.DENY_MODULES in os.environ:
    IAST_DENYLIST += tuple(os.environ[IAST.DENY_MODULES].split(IAST.SEP_MODULES))
    IAST_DENYLIST_LEN = len(IAST_DENYLIST)


ENCODING = ""

log = get_logger(__name__)


def get_encoding(module_path: Text) -> Text:
    """
    First tries to detect the encoding for the file,
    otherwise, returns global encoding default
    """
    global ENCODING
    if not ENCODING:
        try:
            ENCODING = codecs.lookup("utf-8-sig").name
        except LookupError:
            ENCODING = codecs.lookup("utf-8").name
    return ENCODING


_NOT_PATCH_MODULE_NAMES = _stdlib_for_python_version() | set(builtin_module_names)


def _in_python_stdlib(module_name: str) -> bool:
    return module_name.split(".")[0].lower() in [x.lower() for x in _NOT_PATCH_MODULE_NAMES]


def _should_iast_patch(module_name: Text) -> bool:
    """
    select if module_name should be patch from the longest prefix that match in allow or deny list.
    if a prefix is in both list, deny is selected.
    """
    # TODO: A better solution would be to migrate the original algorithm to C++:
    # max_allow = max((len(prefix) for prefix in IAST_ALLOWLIST if module_name.startswith(prefix)), default=-1)
    # max_deny = max((len(prefix) for prefix in IAST_DENYLIST if module_name.startswith(prefix)), default=-1)
    # diff = max_allow - max_deny
    # return diff > 0 or (diff == 0 and not _in_python_stdlib_or_third_party(module_name))
    dotted_module_name = module_name.lower() + "."
    if dotted_module_name.startswith(IAST_ALLOWLIST):
        log.debug("IAST: allowing %s. it's in the IAST_ALLOWLIST", module_name)
        return True

    if re.match(IAST_DENYLIST_REGEX, dotted_module_name):
        log.debug("IAST: denying %s. it's in the IAST_DENYLIST_REGEX", module_name)
        return False
    if IAST_DENYLIST_LEN and dotted_module_name.startswith(IAST_DENYLIST):
        log.debug("IAST: denying %s. it's in the IAST_DENYLIST", module_name)
        return False
    if _in_python_stdlib(module_name):
        log.debug("IAST: denying %s. it's in the _in_python_stdlib", module_name)
        return False
    return True


def visit_ast(
    source_text: Text,
    module_path: Text,
    module_name: Text = "",
) -> Optional[ast.Module]:
    parsed_ast = ast.parse(source_text, module_path)
    _VISITOR.update_location(filename=module_path, module_name=module_name)
    modified_ast = _VISITOR.visit(parsed_ast)

    if not _VISITOR.ast_modified:
        return None

    ast.fix_missing_locations(modified_ast)
    return modified_ast


_DIR_WRAPPER = textwrap.dedent(
    f"""


def {_PREFIX}dir():
    orig_dir = globals().get("{_PREFIX}orig_dir__")

    if orig_dir:
        # Use the original __dir__ method and filter the results
        results = [name for name in orig_dir() if not name.startswith("{_PREFIX}")]
    else:
        # List names from the module's __dict__ and filter out the unwanted names
        results = [
            name for name in globals()
            if not (name.startswith("{_PREFIX}") or name == "__dir__")
        ]

    return results

def {_PREFIX}set_dir_filter():
    if "__dir__" in globals():
        # Store the original __dir__ method
        globals()["{_PREFIX}orig_dir__"] = __dir__

    # Replace the module's __dir__ with the custom one
    globals()["__dir__"] = {_PREFIX}dir

{_PREFIX}set_dir_filter()

    """
)


def astpatch_module(module: ModuleType) -> Tuple[str, Optional[ast.Module]]:
    module_name = module.__name__

    module_origin = origin(module)
    if module_origin is None:
        log.debug("astpatch_source couldn't find the module: %s", module_name)
        return "", None

    module_path = str(module_origin)
    try:
        if module_origin.stat().st_size == 0:
            # Don't patch empty files like __init__.py
            log.debug("empty file: %s", module_path)
            return "", None
    except OSError:
        log.debug("astpatch_source couldn't find the file: %s", module_path, exc_info=True)
        return "", None

    # Get the file extension, if it's dll, os, pyd, dyn, dynlib: return
    # If its pyc or pyo, change to .py and check that the file exists. If not,
    # return with warning.
    _, module_ext = os.path.splitext(module_path)

    if module_ext.lower() not in {".pyo", ".pyc", ".pyw", ".py"}:
        # Probably native or built-in module
        log.debug("extension not supported: %s for: %s", module_ext, module_path)
        return "", None

    with open(module_path, "r", encoding=get_encoding(module_path)) as source_file:
        try:
            source_text = source_file.read()
        except UnicodeDecodeError:
            log.debug("unicode decode error for file: %s", module_path, exc_info=True)
            return "", None

    if len(source_text.strip()) == 0:
        # Don't patch empty files like __init__.py
        log.debug("empty file: %s", module_path)
        return "", None

    if not asbool(os.environ.get(IAST.ENV_NO_DIR_PATCH, "false")) and version_info > (3, 7):
        # Add the dir filter so __ddtrace stuff is not returned by dir(module)
        # does not work in 3.7 because it enters into infinite recursion
        source_text += _DIR_WRAPPER

    new_ast = visit_ast(
        source_text,
        module_path,
        module_name=module_name,
    )
    if new_ast is None:
        log.debug("file not ast patched: %s", module_path)
        return "", None

    return module_path, new_ast
