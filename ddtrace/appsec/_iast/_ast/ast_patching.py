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
IAST_DENYLIST_REGEX = re.compile(
    r"(?:d(?:jango(?:\.(?:co(?:n(?:trib\.(?:a(?:dmin(?:\.(?:views\.(?:autocomplete\.|decorators\.|m"
    r"ain\.)|w(?:agtail_hooks\.|idgets\.)|a(?:ctions\.|dmin\.|pps\.)|image_formats\.|templatetags\."
    r"|decorators\.|exceptions\.|helpers\.|options\.|checks\.|sites\.)|docs\.(?:utils\.|views\.))|u"
    r"th\.(?:c(?:ontext_processors\.|hecks\.)|m(?:anagement\.|iddleware\.)|ba(?:se_user\.|ckends\.)"
    r"|password_validation\.|a(?:dmin\.|pps\.)|image_formats\.|wagtail_hooks\.|templatetags\.|decor"
    r"ators\.|validators\.|hashers\.|signals\.))|s(?:essions\.(?:ba(?:se_session\.|ckends\.)|a(?:dm"
    r"in\.|pps\.)|image_formats\.|wagtail_hooks\.|templatetags\.|exceptions\.|middleware\.)|taticfi"
    r"les\.(?:a(?:dmin\.|pps\.)|image_formats\.|wagtail_hooks\.|templatetags\.|finders\.|storage\.|"
    r"checks\.|models\.|utils\.)|ites\.)|messages\.(?:con(?:text_processors\.|stants\.)|a(?:p(?:ps\."
    r"|i\.)|dmin\.)|image_formats\.|wagtail_hooks\.|templatetags\.|middleware\.|storage\.|utils\.)"
    r"|contenttypes\.(?:m(?:anagement\.|odels\.)|f(?:ields\.|orms\.)|a(?:dmin\.|pps\.)|image_format"
    r"s\.|wagtail_hooks\.|templatetags\.|checks\.|views\.)|humanize\.templatetags\.)|f\.)|re\.(?:c("
    r"?:hecks\.(?:c(?:ompatibility\.(?:django_4_0\.)?|aches\.)|security\.(?:sessions\.|base\.|csrf\."
    r")?|m(?:odel_checks\.|essages\.)|t(?:ranslation\.|emplates\.)|async_checks\.|database\.|regis"
    r"try\.|files\.|urls)|ache\.(?:backends\.|utils\.))|ma(?:nagement\.(?:color\.|base\.|sql\.)|il\."
    r")|exceptions\.|validators\.|paginator\.|signing\.))|te(?:mplate(?:\.(?:l(?:oader(?:_tags\.|s"  # codespell:ignore
    r"\.|\.)|ibrary\.)|context(?:_processors\.|\.)|default(?:filters\.|tags\.)|e(?:xceptions\.|ngin"  # codespell:ignore
    r"e\.)|ba(?:ckends\.|se\.)|autoreload\.|response\.|smartif\.|utils\.)|tags\.)|st\.)|u(?:rls\.(?"
    r":con(?:verters\.|f\.)|exceptions\.|resolvers\.|utils\.|base\.)|tils\.)|apps\.(?:registry\.|co"
    r"nfig\.)|dispatch\.dispatcher\.)|_filters\.(?:rest_framework\.(?:filters(?:et\.|\.)|backends\."
    r")?|co(?:n(?:stants\.|f\.)|mpat\.)|fi(?:lters(?:et\.|\.)|elds\.)|exceptions\.|widgets\.|utils\."
    r"))|i(?:ll\.(?:settings\.|info\.)|fflib\.)|e(?:fusedxml\.|precated\.)|d(?:sketch\.|trace\.)|a"
    r"teutil\.)|c(?:har(?:det\.(?:lang(?:t(?:urkishmodel\.|haimodel\.)|bulgarianmodel\.|russianmode"
    r"l\.|hebrewmodel\.|greekmodel\.)|e(?:uc(?:kr(?:prober\.|freq\.)|tw(?:prober\.|freq\.)|jpprober"
    r"\.)|nums\.|scsm\.)|c(?:harsetgroupprober\.|p949prober\.)|sbc(?:harsetprober\.|sgroupprober\.)"
    r"|mbcs(?:groupprober\.|sm\.)|gb2312(?:prober\.|freq\.)|big5(?:prober\.|freq\.)|hebrewprober\.|"
    r"jisfreq\.)|set_normalizer\.)|o(?:n(?:current\.futures\.|figparser\.)|reschema\.|lorama\.)|r(?"
    r":ispy_forms\.|ypto\.)|e(?:rtifi\.|lery\.)|chardet\.|attrs\.|lick\.|math\.|ffi\.|v2\.)|a(?:syn"
    r"c(?:io\.(?:base_(?:subprocess\.|futures\.|events\.|tasks\.)|s(?:elector_events\.|ubprocess\.|"
    r"taggered\.)|t(?:r(?:ansports\.|sock\.)|hreads\.|asks\.)|co(?:routines\.|nstants\.)|e(?:xcepti"
    r"ons\.|vents\.)|lo(?:cks\.|g\.)|unix_events\.|protocols\.|futures\.|runners\.|queues\.)|pg\.pg"  # codespell:ignore
    r"proto\.)|io(?:http\.(?:_(?:h(?:ttp_(?:parser\.|writer\.)|elpers\.)|websocket\.)|tcp_helpers\."
    r"|log\.)|quic\.)|ttr\.(?:_(?:next_gen\.|config\.)|filters\.|setters\.)|pi_pb2(?:_grpc\.|\.)|ll"
    r"auth\.|nyio\.|mqp\.)|b(?:oto(?:core\.(?:vendored\.requests\.|docs\.bcdoc\.|retries\.)|3\.(?:d"
    r"ocs\.docstring\.|s3\.))|rotli(?:cffi\.|\.)|ackports\.|ytecode\.|linker\.)|p(?:y(?:nndescent\."
    r"|cparser\.|dicom\.|test\.)|sycopg(?:2\.|\.)|kg_resources\.|ackaging\.|rotobuf\.|luggy\.|ip\.)"
    r"|s(?:qlalchemy\.orm\.interfaces\.|etuptools\.|klearn\.|niffio\.|anic\.|cipy\.)|u(?:rlpatterns"
    r"_reverse\.tests\.|v(?:icorn\.|loop\.)|nittest\.mock\.|map\.)|h(?:ttp(?:tools\.|core\.|x\.)|yp"
    r"othesis\.|11\.)|i(?:mportlib_metadata\.|tsdangerous\.|nspect\.)|g(?:oogle(?:cloudsdk\.|\.auth"
    r"\.)|reenlet\.)|w(?:e(?:bsocket(?:s\.|\.)|rkzeug\.)|rapt\.)|e(?:xceptiongroup\.|nvier\.)|_p(?:"
    r"sycopg\.|ytest\.)|f(?:reezegun\.|lask\.)|m(?:atplotlib\.|oto\.)|n(?:ibabel\.|umpy\.)|opentele"
    r"metry\-api\.|typing_extensions\.|r(?:edis\.|ich\.)|kombu\.|zipp\.|PIL\.)",
    re.IGNORECASE,
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
    dotted_module_name = module_name + "."

    if IAST_DENYLIST_REGEX.match(dotted_module_name):
        log.debug("IAST: denying %s. it's in the IAST_DENYLIST_REGEX", module_name)
        return False

    dotted_module_name = dotted_module_name.lower()

    if dotted_module_name.startswith(IAST_ALLOWLIST):
        log.debug("IAST: allowing %s. it's in the IAST_ALLOWLIST", module_name)
        return True

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
