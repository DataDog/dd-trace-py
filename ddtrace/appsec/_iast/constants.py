import re


VULN_INSECURE_HASHING_TYPE = "WEAK_HASH"
VULN_WEAK_CIPHER_TYPE = "WEAK_CIPHER"
VULN_SQL_INJECTION = "SQL_INJECTION"
VULN_PATH_TRAVERSAL = "PATH_TRAVERSAL"
VULN_WEAK_RANDOMNESS = "WEAK_RANDOMNESS"
VULN_INSECURE_COOKIE = "INSECURE_COOKIE"
VULN_NO_HTTPONLY_COOKIE = "NO_HTTPONLY_COOKIE"
VULN_NO_SAMESITE_COOKIE = "NO_SAMESITE_COOKIE"
VULN_CMDI = "COMMAND_INJECTION"
VULN_HEADER_INJECTION = "HEADER_INJECTION"
VULN_UNVALIDATED_REDIRECT = "UNVALIDATED_REDIRECT"
VULN_CODE_INJECTION = "CODE_INJECTION"
VULN_XSS = "XSS"
VULN_SSRF = "SSRF"
VULN_STACKTRACE_LEAK = "STACKTRACE_LEAK"

HEADER_NAME_VALUE_SEPARATOR = ": "

MD5_DEF = "md5"
SHA1_DEF = "sha1"

DES_DEF = "des"
BLOWFISH_DEF = "blowfish"
RC2_DEF = "rc2"
RC4_DEF = "rc4"
IDEA_DEF = "idea"
STACKTRACE_RE_DETECT = re.compile(r"Traceback \(most recent call last\):")
HTML_TAGS_REMOVE = re.compile(r"<!--[\s\S]*?-->|<[^>]*>|&#\w+;")
STACKTRACE_FILE_LINE = re.compile(r"File (.*?), line (\d+), in (.+)")
STACKTRACE_EXCEPTION_REGEX = re.compile(
    r"^(?P<exc>[A-Za-z_]\w*(?:Error|Exception|Interrupt|Fault|Warning))" r"(?:\s*:\s*(?P<msg>.*))?$"
)

DEFAULT_WEAK_HASH_ALGORITHMS = {MD5_DEF, SHA1_DEF}

DEFAULT_WEAK_CIPHER_ALGORITHMS = {DES_DEF, BLOWFISH_DEF, RC2_DEF, RC4_DEF, IDEA_DEF}

DEFAULT_WEAK_RANDOMNESS_FUNCTIONS = {
    "random",
    "randint",
    "randrange",
    "choice",
    "shuffle",
    "betavariate",
    "gammavariate",
    "expovariate",
    "choices",
    "gauss",
    "uniform",
    "lognormvariate",
    "normalvariate",
    "paretovariate",
    "sample",
    "triangular",
    "vonmisesvariate",
    "weibullvariate",
    "randbytes",
}

DEFAULT_PATH_TRAVERSAL_FUNCTIONS = {
    "_io": {"open"},
    "io": {"open"},
    "glob": {"glob"},
    "os": {
        "mkdir",
        "remove",
        "rename",
        "rmdir",
        "listdir",
    },
    "pickle": {"load"},
    "_pickle": {"load"},
    "posix": {
        "mkdir",
        "remove",
        "rename",
        "rmdir",
        "listdir",
    },
    "shutil": {
        "copy",
        "copytree",
        "move",
        "rmtree",
    },
    "tarfile": {"open"},
    "zipfile": {"ZipFile"},
}
DBAPI_SQLITE = "sqlite"
DBAPI_PSYCOPG = "psycopg"
DBAPI_MYSQL = "mysql"
DBAPI_MYSQLDB = "mysqldb"
DBAPI_PYMYSQL = "pymysql"
DBAPI_MARIADB = "mariadb"
DBAPI_INTEGRATIONS = (DBAPI_SQLITE, DBAPI_PSYCOPG, DBAPI_MYSQL, DBAPI_MYSQLDB, DBAPI_MARIADB, DBAPI_PYMYSQL)

DEFAULT_SOURCE_IO_FUNCTIONS = {
    "_io": {"read"},
}
