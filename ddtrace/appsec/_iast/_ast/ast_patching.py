#!/usr/bin/env python3

import ast
import codecs
import os
from sys import builtin_module_names
import textwrap
from types import ModuleType
from typing import Iterable
from typing import Optional
from typing import Set
from typing import Text
from typing import Tuple

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._python_info.stdlib import _stdlib_for_python_version
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import origin
from ddtrace.internal.packages import get_package_distributions
from ddtrace.internal.utils.formats import asbool

from .._logs import iast_ast_debug_log
from .._logs import iast_compiling_debug_log
from .visitor import AstVisitor


_VISITOR = AstVisitor()

_PREFIX = IAST.PATCH_ADDED_SYMBOL_PREFIX

# Prefixes for modules where IAST patching is allowed
# Only packages that have the test_propagation=True in test_packages and are not in the denylist must be here
IAST_ALLOWLIST: Tuple[Text, ...] = (
    "attrs.",
    "beautifulsoup4.",
    "cachetools.",
    "cryptography.",
    "django.",
    "docutils.",
    "idna.",
    "iniconfig.",
    "jinja2.",
    "lxml.",
    "multidict.",
    "platformdirs",
    "pygments.",
    "pynacl.",
    "pyparsing.",
    "multipart",
    "sqlalchemy.",
    "tomli",
    "yarl.",
)

# NOTE: For testing reasons, don't add astunparse here, see test_ast_patching.py
IAST_DENYLIST: Tuple[Text, ...] = (
    "_psycopg.",  # PostgreSQL adapter for Python (v3)
    "_pytest.",
    "aiohttp._helpers.",
    "aiohttp._http_parser.",
    "aiohttp._http_writer.",
    "aiohttp._websocket.",
    "aiohttp.log.",
    "aiohttp.tcp_helpers.",
    "aioquic.",
    "altgraph.",
    "anyio.",
    "api_pb2.",  # Patching crashes with these auto-generated modules, propagation is not needed
    "api_pb2_grpc.",  # Patching crashes with these auto-generated modules, propagation is not needed
    "asyncio.base_events.",
    "asyncio.base_futures.",
    "asyncio.base_subprocess.",
    "asyncio.base_tasks.",
    "asyncio.constants.",
    "asyncio.coroutines.",
    "asyncio.events.",
    "asyncio.exceptions.",
    "asyncio.futures.",
    "asyncio.locks.",
    "asyncio.log.",
    "asyncio.protocols.",
    "asyncio.queues.",
    "asyncio.runners.",
    "asyncio.selector_events.",
    "asyncio.staggered.",
    "asyncio.subprocess.",
    "asyncio.tasks.",
    "asyncio.threads.",
    "asyncio.transports.",
    "asyncio.trsock.",
    "asyncio.unix_events.",
    "asyncpg.pgproto.",
    "attr._config.",
    "attr._next_gen.",
    "attr.filters.",
    "attr.setters.",
    "autopep8.",
    "backports.",
    "black.",
    "blinker.",
    "boto3.docs.docstring.",
    "boto3.s3.",
    "botocore.docs.bcdoc.",
    "botocore.retries.",
    "botocore.vendored.requests.",
    "brotli.",
    "brotlicffi.",
    "bytecode.",
    "cattrs.",
    "cchardet.",
    "certifi.",
    "cffi.",
    "chardet.big5freq.",
    "chardet.big5prober.",
    "chardet.charsetgroupprober.",
    "chardet.cp949prober.",
    "chardet.enums.",
    "chardet.escsm.",
    "chardet.eucjpprober.",
    "chardet.euckrfreq.",
    "chardet.euckrprober.",
    "chardet.euctwfreq.",
    "chardet.euctwprober.",
    "chardet.gb2312freq.",
    "chardet.gb2312prober.",
    "chardet.hebrewprober.",
    "chardet.jisfreq.",
    "chardet.langbulgarianmodel.",
    "chardet.langgreekmodel.",
    "chardet.langhebrewmodel.",
    "chardet.langrussianmodel.",
    "chardet.langthaimodel.",
    "chardet.langturkishmodel.",
    "chardet.mbcsgroupprober.",
    "chardet.mbcssm.",
    "chardet.sbcharsetprober.",
    "chardet.sbcsgroupprober.",
    "charset_normalizer.",
    "click.",
    "cmath.",
    "colorama.",
    "concurrent.futures.",
    "configparser.",
    "contourpy.",
    "coreschema.",
    "crispy_forms.",
    "crypto.",  # This module is patched by the IAST patch methods, propagation is not needed
    "cx_logging.",
    "cycler.",
    "cython.",
    "dateutil.",
    "dateutil.",
    "ddsketch.",
    "ddtrace.",
    "defusedxml.",
    "deprecated.",
    "difflib.",
    "dill.info.",
    "dill.settings.",
    "dipy.",
    "django.apps.config.",
    "django.apps.registry.",
    "django.conf.",
    "django.contrib.admin.actions.",
    "django.contrib.admin.admin.",
    "django.contrib.admin.apps.",
    "django.contrib.admin.checks.",
    "django.contrib.admin.decorators.",
    "django.contrib.admin.exceptions.",
    "django.contrib.admin.helpers.",
    "django.contrib.admin.image_formats.",
    "django.contrib.admin.options.",
    "django.contrib.admin.sites.",
    "django.contrib.admin.templatetags.",
    "django.contrib.admin.views.autocomplete.",
    "django.contrib.admin.views.decorators.",
    "django.contrib.admin.views.main.",
    "django.contrib.admin.wagtail_hooks.",
    "django.contrib.admin.widgets.",
    "django.contrib.admindocs.utils.",
    "django.contrib.admindocs.views.",
    "django.contrib.auth.admin.",
    "django.contrib.auth.apps.",
    "django.contrib.auth.backends.",
    "django.contrib.auth.base_user.",
    "django.contrib.auth.checks.",
    "django.contrib.auth.context_processors.",
    "django.contrib.auth.decorators.",
    "django.contrib.auth.hashers.",
    "django.contrib.auth.image_formats.",
    "django.contrib.auth.management.",
    "django.contrib.auth.middleware.",
    "django.contrib.auth.password_validation.",
    "django.contrib.auth.signals.",
    "django.contrib.auth.templatetags.",
    "django.contrib.auth.validators.",
    "django.contrib.auth.wagtail_hooks.",
    "django.contrib.contenttypes.admin.",
    "django.contrib.contenttypes.apps.",
    "django.contrib.contenttypes.checks.",
    "django.contrib.contenttypes.fields.",
    "django.contrib.contenttypes.forms.",
    "django.contrib.contenttypes.image_formats.",
    "django.contrib.contenttypes.management.",
    "django.contrib.contenttypes.models.",
    "django.contrib.contenttypes.templatetags.",
    "django.contrib.contenttypes.views.",
    "django.contrib.contenttypes.wagtail_hooks.",
    "django.contrib.humanize.templatetags.",
    "django.contrib.messages.admin.",
    "django.contrib.messages.api.",
    "django.contrib.messages.apps.",
    "django.contrib.messages.constants.",
    "django.contrib.messages.context_processors.",
    "django.contrib.messages.image_formats.",
    "django.contrib.messages.middleware.",
    "django.contrib.messages.storage.",
    "django.contrib.messages.templatetags.",
    "django.contrib.messages.utils.",
    "django.contrib.messages.wagtail_hooks.",
    "django.contrib.sessions.admin.",
    "django.contrib.sessions.apps.",
    "django.contrib.sessions.backends.",
    "django.contrib.sessions.base_session.",
    "django.contrib.sessions.exceptions.",
    "django.contrib.sessions.image_formats.",
    "django.contrib.sessions.middleware.",
    "django.contrib.sessions.templatetags.",
    "django.contrib.sessions.wagtail_hooks.",
    "django.contrib.sites.",
    "django.contrib.staticfiles.admin.",
    "django.contrib.staticfiles.apps.",
    "django.contrib.staticfiles.checks.",
    "django.contrib.staticfiles.finders.",
    "django.contrib.staticfiles.image_formats.",
    "django.contrib.staticfiles.models.",
    "django.contrib.staticfiles.storage.",
    "django.contrib.staticfiles.templatetags.",
    "django.contrib.staticfiles.utils.",
    "django.contrib.staticfiles.wagtail_hooks.",
    "django.core.cache.backends.",
    "django.core.cache.utils.",
    "django.core.checks.async_checks.",
    "django.core.checks.caches.",
    "django.core.checks.compatibility.",
    "django.core.checks.compatibility.django_4_0.",
    "django.core.checks.database.",
    "django.core.checks.files.",
    "django.core.checks.messages.",
    "django.core.checks.model_checks.",
    "django.core.checks.registry.",
    "django.core.checks.security.",
    "django.core.checks.security.base.",
    "django.core.checks.security.csrf.",
    "django.core.checks.security.sessions.",
    "django.core.checks.templates.",
    "django.core.checks.translation.",
    "django.core.checks.urls",
    "django.core.exceptions.",
    "django.core.mail.",
    "django.core.management.base.",
    "django.core.management.color.",
    "django.core.management.sql.",
    "django.core.paginator.",
    "django.core.signing.",
    "django.core.validators.",
    "django.dispatch.dispatcher.",
    "django.template.autoreload.",
    "django.template.backends.",
    "django.template.base.",
    "django.template.context.",
    "django.template.context_processors.",
    "django.template.defaultfilters.",
    "django.template.defaulttags.",
    "django.template.engine.",
    "django.template.exceptions.",
    "django.template.library.",
    "django.template.loader.",
    "django.template.loader_tags.",
    "django.template.loaders.",
    "django.template.response.",
    "django.template.smartif.",
    "django.template.utils.",
    "django.templatetags.",
    "django.test.",
    "django.urls.base.",
    "django.urls.conf.",
    "django.urls.converters.",
    "django.urls.exceptions.",
    "django.urls.resolvers.",
    "django.urls.utils.",
    "django.utils.",
    "django_filters.compat.",
    "django_filters.conf.",
    "django_filters.constants.",
    "django_filters.exceptions.",
    "django_filters.fields.",
    "django_filters.filters.",
    "django_filters.filterset.",
    "django_filters.rest_framework.",
    "django_filters.rest_framework.backends.",
    "django_filters.rest_framework.filters.",
    "django_filters.rest_framework.filterset.",
    "django_filters.utils.",
    "django_filters.widgets.",
    "dnspython.",
    "elasticdeform.",
    "envier.",
    "exceptiongroup.",
    "flask.",
    "fonttools.",
    "freezegun.",  # Testing utilities for time manipulation
    "google.auth.",
    "googlecloudsdk.",
    "gprof2dot.",
    "h11.",
    "h5py.",
    "httpcore.",
    "httptools.",
    "httpx.",
    "hypothesis.",  # Testing utilities
    "imageio.",
    "importlib_metadata.",
    "inspect.",  # this package is used to get the stack frames, propagation is not needed
    "itsdangerous.",
    "kiwisolver.",
    "matplotlib.",
    "moto.",  # used for mocking AWS, propagation is not needed
    "mypy.",
    "mypy_extensions.",
    "networkx.",
    "nibabel.",
    "nilearn.",
    "numba.",
    "numpy.",
    "opentelemetry-api.",
    "packaging.",
    "pandas.",
    "pdf2image.",
    "pefile.",
    "pil.",
    "pip.",
    "pkg_resources.",
    "pluggy.",
    "protobuf.",
    "psycopg.",  # PostgreSQL adapter for Python (v3)
    "psycopg2.",  # PostgreSQL adapter for Python (v2)
    "pycodestyle.",
    "pycparser.",  # this package is called when a module is imported, propagation is not needed
    "pydicom.",
    "pyinstaller.",
    "pynndescent.",
    "pystray.",
    "pytest.",  # Testing framework
    "pytz.",
    "rich.",
    "sanic.",
    "scipy.",
    "setuptools.",
    "silk.",  # django-silk package
    "skbase.",
    "sklearn.",  # Machine learning library
    "sniffio.",
    "sqlalchemy.orm.interfaces.",  # Performance optimization
    "threadpoolctl.",
    "tifffile.",
    "tqdm.",
    "trx.",
    "typing_extensions.",
    "umap.",
    "unittest.mock.",
    "urlpatterns_reverse.tests.",  # assertRaises eat exceptions in native code, so we don't call the original function
    "uvicorn.",
    "uvloop.",
    "wcwidth.",
    "websocket.",
    "websockets.",
    "werkzeug.",
    "win32ctypes.",
    "wrapt.",
    "xlib.",
    "zipp.",
)

USER_ALLOWLIST = tuple(os.environ.get(IAST.PATCH_MODULES, "").split(IAST.SEP_MODULES))
USER_DENYLIST = tuple(os.environ.get(IAST.DENY_MODULES, "").split(IAST.SEP_MODULES))

ENCODING = ""

log = get_logger(__name__)


class _TrieNode:
    __slots__ = ("children", "is_end")

    def __init__(self):
        self.children = {}
        self.is_end = False

    def __iter__(self):
        if self.is_end:
            yield ("", None)
        else:
            for k, v in self.children.items():
                yield (k, dict(v))


def build_trie(words: Iterable[str]) -> _TrieNode:
    root = _TrieNode()
    for word in words:
        node = root
        for char in word:
            if char not in node.children:
                node.children[char] = _TrieNode()
            node = node.children[char]
        node.is_end = True
    return root


_TRIE_ALLOWLIST = build_trie(IAST_ALLOWLIST)
_TRIE_DENYLIST = build_trie(IAST_DENYLIST)
_TRIE_USER_ALLOWLIST = build_trie(USER_ALLOWLIST)
_TRIE_USER_DENYLIST = build_trie(USER_DENYLIST)


def _trie_has_prefix_for(trie: _TrieNode, string: str) -> bool:
    node = trie
    for char in string:
        node = node.children.get(char)
        if not node:
            return False

        if node.is_end:
            return True
    return node.is_end


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


_NOT_PATCH_MODULE_NAMES = {i.lower() for i in _stdlib_for_python_version() | set(builtin_module_names)}

_IMPORTLIB_PACKAGES: Set[str] = set()


def _in_python_stdlib(module_name: str) -> bool:
    return module_name.split(".")[0].lower() in _NOT_PATCH_MODULE_NAMES


def _is_first_party(module_name: str):
    global _IMPORTLIB_PACKAGES
    if "vendor." in module_name or "vendored." in module_name:
        return False

    if not _IMPORTLIB_PACKAGES:
        _IMPORTLIB_PACKAGES = set(get_package_distributions())

    return module_name.split(".")[0] not in _IMPORTLIB_PACKAGES


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
    if _in_python_stdlib(module_name):
        iast_ast_debug_log(f"denying {module_name}. it's in the python_stdlib")
        return False

    if _is_first_party(module_name):
        iast_ast_debug_log(f"allowing {module_name}. it's a first party module")
        return True

    # else: third party. Check that is in the allow list and not in the deny list
    dotted_module_name = module_name.lower() + "."

    # User allow or deny list set by env var have priority
    if _trie_has_prefix_for(_TRIE_USER_ALLOWLIST, dotted_module_name):
        iast_ast_debug_log(f"allowing {module_name}. it's in the USER_ALLOWLIST")
        return True

    if _trie_has_prefix_for(_TRIE_USER_DENYLIST, dotted_module_name):
        iast_ast_debug_log(f"denying {module_name}. it's in the USER_DENYLIST")
        return False

    if _trie_has_prefix_for(_TRIE_ALLOWLIST, dotted_module_name):
        if _trie_has_prefix_for(_TRIE_DENYLIST, dotted_module_name):
            iast_ast_debug_log(f"denying {module_name}. it's in the DENYLIST")
            return False
        iast_ast_debug_log(f"allowing {module_name}. it's in the ALLOWLIST")
        return True
    iast_ast_debug_log(f"denying {module_name}. it's NOT in the ALLOWLIST")
    return False


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
        iast_compiling_debug_log(f"could not find the module: {module_name}")
        return "", None

    module_path = str(module_origin)
    try:
        if module_origin.stat().st_size == 0:
            # Don't patch empty files like __init__.py
            iast_compiling_debug_log(f"empty file: {module_path}")
            return "", None
    except OSError:
        iast_compiling_debug_log(f"could not find the file: {module_path}", exc_info=True)
        return "", None

    # Get the file extension, if it's dll, os, pyd, dyn, dynlib: return
    # If its pyc or pyo, change to .py and check that the file exists. If not,
    # return with warning.
    _, module_ext = os.path.splitext(module_path)

    if module_ext.lower() not in {".pyo", ".pyc", ".pyw", ".py"}:
        # Probably native or built-in module
        iast_compiling_debug_log(f"Extension not supported: {module_ext} for: {module_path}")
        return "", None

    with open(module_path, "r", encoding=get_encoding(module_path)) as source_file:
        try:
            source_text = source_file.read()
        except UnicodeDecodeError:
            iast_compiling_debug_log(f"Encode decode error for file: {module_path}", exc_info=True)
            return "", None
        except Exception:
            iast_compiling_debug_log(f"Unexpected read error: {module_path}", exc_info=True)
            return "", None

    if len(source_text.strip()) == 0:
        # Don't patch empty files like __init__.py
        iast_compiling_debug_log(f"Empty file: {module_path}")
        return "", None

    if not asbool(os.environ.get(IAST.ENV_NO_DIR_PATCH, "false")):
        # Add the dir filter so __ddtrace stuff is not returned by dir(module)
        source_text += _DIR_WRAPPER

    new_ast = visit_ast(
        source_text,
        module_path,
        module_name=module_name,
    )
    if new_ast is None:
        iast_compiling_debug_log(f"file not ast patched: {module_path}")
        return "", None

    return module_path, new_ast
