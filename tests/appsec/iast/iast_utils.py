import re
import zlib

from ddtrace.internal.compat import PY2


def get_line(label, filename=None):
    """get the line number after the label comment in source file `filename`"""
    with open(filename, "r") as file_in:
        for nb_line, line in enumerate(file_in):
            if re.search("label " + re.escape(label), line):
                return nb_line + 2
    assert False, "label %s not found" % label


def get_line_and_hash(label, vuln_type, filename=None):
    """return the line number and the associated vulnerability hash for `label` and source file `filename`"""

    line = get_line(label, filename=filename)
    rep = "Vulnerability(type='%s', location=Location(path='%s', line=%s))" % (vuln_type, filename, line)
    hash_value = zlib.crc32(rep.encode())
    if PY2 and hash_value < 0:
        hash_value += 1 << 32

    return line, hash_value
