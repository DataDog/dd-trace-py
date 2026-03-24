import re


ENTRYPOINT_NAME_TAG = "entrypoint.name"
ENTRYPOINT_WORKDIR_TAG = "entrypoint.workdir"
ENTRYPOINT_TYPE_TAG = "entrypoint.type"
ENTRYPOINT_TYPE_SCRIPT = "script"
ENTRYPOINT_BASEDIR_TAG = "entrypoint.basedir"
SVC_USER_TAG = "svc.user"
SVC_AUTO_TAG = "svc.auto"
INFO_RETRY_MAX_ATTEMPTS = 3

_CONSECUTIVE_UNDERSCORES_PATTERN = re.compile(r"_{2,}")
_ALLOWED_CHARS = _ALLOWED_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789/._-")
MAX_LENGTH = 100

INFO_RETRY_MAX_ATTEMPTS = 3
