import importlib
import re
import types
from typing import Optional
from typing import Text
import zlib

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._ast.ast_patching import astpatch_module
from ddtrace.appsec._iast._ast.ast_patching import iastpatch


# Check if the log contains "iast::" to raise an error if that’s the case BUT, if the logs contains
# "iast::instrumentation::" or "iast::instrumentation::"
# are valid logs
IAST_VALID_LOG = re.compile(r"^iast::(?!instrumentation::|propagation::context::).*$")


class IastTestException(Exception):
    pass


def get_line(label: Text, filename: Optional[Text] = None):
    """get the line number after the label comment in source file `filename`"""
    with open(filename, "r") as file_in:
        for nb_line, line in enumerate(file_in):
            if re.search("label " + re.escape(label), line):
                return nb_line + 2
    raise AssertionError("label %s not found" % label)


def get_line_and_hash(label: Text, vuln_type: Text, filename=None, fixed_line=None):
    """return the line number and the associated vulnerability hash for `label` and source file `filename`"""

    if fixed_line is not None:
        line = fixed_line
    else:
        line = get_line(label, filename=filename)
    rep = "Vulnerability(type='%s', location=Location(path='%s', line=%s))" % (vuln_type, filename, line)
    hash_value = zlib.crc32(rep.encode())

    return line, hash_value


def _iast_patched_module_and_patched_source(module_name, new_module_object=False):
    module = importlib.import_module(module_name)
    module_path, patched_source = astpatch_module(module)
    compiled_code = compile(patched_source, module_path, "exec")
    module_changed = types.ModuleType(module_name) if new_module_object else module
    exec(compiled_code, module_changed.__dict__)
    return module_changed, patched_source


def _iast_patched_module(module_name, new_module_object=False):
    iastpatch.build_list_from_env(IAST.PATCH_MODULES)
    iastpatch.build_list_from_env(IAST.DENY_MODULES)
    res = iastpatch.should_iast_patch(module_name)
    if res >= iastpatch.ALLOWED_USER_ALLOWLIST:
        module, _ = _iast_patched_module_and_patched_source(module_name, new_module_object)
    else:
        raise IastTestException(f"IAST Test Error: module {module_name} was excluded: {res}")
    return module
