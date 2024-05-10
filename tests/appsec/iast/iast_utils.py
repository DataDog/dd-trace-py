import re
from typing import Optional
from typing import Text
import zlib


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
