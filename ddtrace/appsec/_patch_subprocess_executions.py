import collections
from fnmatch import fnmatch
import os
import re
import shlex
import subprocess
from threading import RLock
from typing import List
from typing import Tuple
from typing import Union

import attr

from ddtrace import Pin
from ddtrace import config
from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanTypes
from ddtrace.internal import _context
from ddtrace.internal.compat import shjoin, PY2
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


"""
JJJ TODO:
- make sure than _unpatch is called at the right time inside the tracer
- flask snapshot tests from views
- python2 testing
- mypy
- exception handlers so it never fails and always executes the command
- constants for the tag names
- rebase
"""


def _patch():
    # type: () -> None
    if not config._appsec_enabled:
        return

    import os

    Pin().onto(os)
    trace_utils.wrap(os, "system", traced_ossystem(os))

    # all os.spawn* variants eventually use this one:
    trace_utils.wrap(os, "_spawnvef", traced_osspawn(os))

    if PY2:
        # note: popen* uses subprocess in Python3, which we already wrap below, but not in
        # Python2
        trace_utils.wrap(os, "popen", traced_py2popen(os))
        trace_utils.wrap(os, "popen2", traced_py2popen(os))
        trace_utils.wrap(os, "popen3", traced_py2popen(os))
        trace_utils.wrap(os, "popen4", traced_py2popen(os))

    Pin().onto(subprocess)
    # We store the parameters on __init__ in the context and set the tags on wait
    # (where all the Popen objects eventually arrive, unless killed before it)
    trace_utils.wrap(subprocess, "Popen.__init__", traced_subprocess_init(subprocess))
    trace_utils.wrap(subprocess, "Popen.wait", traced_subprocess_wait(subprocess))


def _unpatch():
    # type: () -> None
    trace_utils.unwrap(os, "system")
    trace_utils.unwrap(os, "_spawnvef")
    trace_utils.unwrap(subprocess.Popen, "__init__")
    trace_utils.unwrap(subprocess.Popen, "wait")


@attr.s(eq=False)
class SubprocessCmdLineCacheEntry(object):
    binary = attr.ib(type=str, default=None)
    arguments = attr.ib(type=List, default=None)
    truncated = attr.ib(type=bool, default=False)
    env_vars = attr.ib(type=List, default=None)
    as_list = attr.ib(type=List, default=None)
    as_string = attr.ib(type=str, default=None)


class SubprocessCmdLine(object):
    # This catches the computed values into a SubprocessCmdLineCacheEntry object
    _CACHE = {}
    _CACHE_DEQUE = collections.deque()
    _CACHE_MAXSIZE = 32
    _CACHE_LOCK = RLock()

    @classmethod
    def _add_new_cache_entry(cls, key, env_vars, binary, arguments, truncated):
        if key in cls._CACHE:
            return

        cache_entry = SubprocessCmdLineCacheEntry()
        cache_entry.binary = binary
        cache_entry.arguments = arguments
        cache_entry.truncated = truncated
        cache_entry.env_vars = env_vars

        with cls._CACHE_LOCK:
            if len(cls._CACHE_DEQUE) >= cls._CACHE_MAXSIZE:
                # If the cache is full, remove the oldest entry
                last_cache_key = cls._CACHE_DEQUE[-1]
                del cls._CACHE[last_cache_key]
                cls._CACHE_DEQUE.pop()

            cls._CACHE[key] = cache_entry
            cls._CACHE_DEQUE.appendleft(key)

        return cache_entry

    @classmethod
    def _clear_cache(cls):
        with cls._CACHE_LOCK:
            cls._CACHE_DEQUE.clear()
            cls._CACHE.clear()

    TRUNCATE_LIMIT = 4 * 1024

    ENV_VARS_ALLOWLIST = {"LD_PRELOAD", "LD_LIBRARY_PATH", "PATH"}

    BINARIES_DENYLIST = {
        "md5",
    }

    SENSITIVE_WORDS_WILDCARDS = (
        "*password*",
        "*passwd*",
        "*mysql_pwd*",
        "*access_token*",
        "*auth_token*",
        "*api_key*",
        "*apikey*",
        "*secret*",
        "*credentials*",
        "stripetoken",
    )
    _COMPILED_ENV_VAR_REGEXP = re.compile(r"\b[A-Z_]+=\w+")

    def __init__(self, shell_args, as_list=False, shell=False):
        # type: (Union[str, list], bool, bool) -> None

        cache_key = str(shell_args) + str(shell)
        self._cache_entry = SubprocessCmdLine._CACHE.get(cache_key)
        if self._cache_entry:
            self.env_vars = self._cache_entry.env_vars
            self.binary = self._cache_entry.binary
            self.arguments = self._cache_entry.arguments
            self.truncated = self._cache_entry.truncated
            return

        self.env_vars = []
        self.binary = ""
        self.arguments = []
        self.truncated = False

        tokens = shell_args if as_list else shlex.split(shell_args)

        # Extract previous environment variables, removing all the ones not
        # in ENV_VARS_ALLOWLIST
        if shell:
            self.scrub_env_vars(tokens)
        else:
            self.binary = tokens[0]
            self.arguments = tokens[1:]

        self.arguments = list(self.arguments) if isinstance(self.arguments, tuple) else self.arguments
        self.scrub_arguments()

        # Create a new cache entry to store the computed values except as_list
        # and as_string that are computed and stored lazily
        self._cache_entry = SubprocessCmdLine._add_new_cache_entry(
            cache_key, self.env_vars, self.binary, self.arguments, self.truncated
        )

    def scrub_env_vars(self, tokens):
        for idx, token in enumerate(tokens):
            if re.match(self._COMPILED_ENV_VAR_REGEXP, token):
                var, value = token.split("=")
                if var in self.ENV_VARS_ALLOWLIST:
                    self.env_vars.append(token)
                else:
                    # scrub the value
                    self.env_vars.append("%s=?" % var)
            else:
                # Next after vars are the binary and arguments
                try:
                    self.binary = tokens[idx]
                    self.arguments = tokens[idx + 1 :]
                except IndexError:
                    pass
                break

    def scrub_arguments(self):
        # if the binary is in the denylist, scrub all arguments
        if self.binary.lower() in self.BINARIES_DENYLIST:
            self.arguments = ["?" for _ in self.arguments]
            return

        param_prefixes = ("-", "/")
        # Scrub case by case
        new_args = []
        deque_args = collections.deque(self.arguments)
        while deque_args:
            current = deque_args[0]
            for sensitive in self.SENSITIVE_WORDS_WILDCARDS:
                if fnmatch(current, sensitive):
                    is_sensitive = True
                    break
            else:
                is_sensitive = False

            if not is_sensitive:
                new_args.append(current)
                deque_args.popleft()
                continue

            # sensitive
            if current[0] not in param_prefixes:
                # potentially not argument, scrub it anyway if it matches a sensitive word
                new_args.append("?")
                deque_args.popleft()
                continue

            # potential --argument
            if "=" in current:
                # contains "=" like in "--password=foo", scrub it just in case
                new_args.append("?")
                deque_args.popleft()
                continue

            try:
                if deque_args[1][0] in param_prefixes:
                    # Next is another option scrub only the current one
                    new_args.append("?")
                    deque_args.popleft()
                    continue
                else:
                    # Next is not an option but potentially a value, scrub it instead
                    new_args.extend([current, "?"])
                    deque_args.popleft()
                    deque_args.popleft()
                    continue
            except IndexError:
                # No next argument, scrub this one just in case since it's sensitive
                new_args.append("?")
                deque_args.popleft()

        self.arguments = new_args

    def truncate_string(self, str_):
        # type: (str) -> str
        # spaced_added is to account for spaces that would not occupy
        # space on a list result
        oversize = len(str_) - self.TRUNCATE_LIMIT

        if oversize <= 0:
            self.truncated = False
            return str_

        self.truncated = True

        msg = ' "4kB argument truncated by %d characters"' % oversize
        return str_[0 : -(oversize + len(msg))] + msg

    def _as_list_and_string(self):
        # type: () -> Tuple[list[str], str]

        total_list = self.env_vars + [self.binary] + self.arguments
        truncated_str = self.truncate_string(shjoin(total_list))
        truncated_list = shlex.split(truncated_str)
        return truncated_list, truncated_str

    def as_list(self):
        if self._cache_entry.as_list is not None:
            return self._cache_entry.as_list

        list_res, str_res = self._as_list_and_string()
        self._cache_entry.as_list = list_res
        self._cache_entry.as_string = str_res
        return list_res

    def as_string(self):
        if self._cache_entry.as_string is not None:
            return self._cache_entry.as_string

        list_res, str_res = self._as_list_and_string()
        self._cache_entry.as_list = list_res
        self._cache_entry.as_string = str_res
        return str_res


@trace_utils.with_traced_module
def traced_ossystem(module, pin, wrapped, instance, args, kwargs):
    with pin.tracer.trace("os.system", span_type=SpanTypes.SYSTEM) as span:
        shellcmd = SubprocessCmdLine(args[0], shell=True)
        span.set_tag_str("name", "command_execution")
        span.set_tag_str("cmd.shell", shellcmd.as_string())
        if shellcmd.truncated:
            span.set_tag_str("cmd.truncated", "yes")
        span.set_tag_str("component", "os")
        span.set_tag_str("resource", shellcmd.binary)
        ret = wrapped(*args, **kwargs)
        span.set_tag_str("cmd.exit_code", str(ret))
    return ret


@trace_utils.with_traced_module
def traced_osspawn(module, pin, wrapped, instance, args, kwargs):
    with pin.tracer.trace("os.spawn", span_type=SpanTypes.SYSTEM) as span:
        span.set_tag_str("name", "command_execution")
        mode, file, func_args, _, _ = args

        shellcmd = SubprocessCmdLine(func_args, as_list=True, shell=False)
        span.set_tag("cmd.exec", shellcmd.as_list())
        if shellcmd.truncated:
            span.set_tag_str("cmd.truncated", "true")
        span.set_tag_str("component", "os")
        span.set_tag_str("resource", shellcmd.binary)

        if mode == os.P_WAIT:
            ret = wrapped(*args, **kwargs)
            span.set_tag_str("cmd.exit_code", str(ret))
            return ret

        return wrapped(*args, **kwargs)


@trace_utils.with_traced_module
def traced_py2popen(module, pin, wrapped, instance, args, kwargs):
    with pin.tracer.trace("os.popen", span_type=SpanTypes.SYSTEM) as span:
        span.set_tag_str("name", "command_execution")
        command = args[0]

        subcmd = SubprocessCmdLine(command, as_list=False, shell=False)
        span.set_tag("cmd.exec", subcmd.as_list())
        if subcmd.truncated:
            span.set_tag_str("cmd.truncated", "true")
        span.set_tag_str("component", "os")
        span.set_tag_str("resource", subcmd.binary)

        return wrapped(*args, **kwargs)



@trace_utils.with_traced_module
def traced_subprocess_init(module, pin, wrapped, instance, args, kwargs):
    with pin.tracer.trace("subprocess.Popen.init", span_type=SpanTypes.SYSTEM) as span:
        cmd_args = args[0] if len(args) else kwargs["args"]
        cmd_args_list = shlex.split(cmd_args) if isinstance(cmd_args, str) else cmd_args
        is_shell = kwargs.get("shell", False)
        _context.set_item("subprocess_popen_is_shell", is_shell, span=span)

        shellcmd = SubprocessCmdLine(cmd_args_list, True, shell=is_shell)
        if shellcmd.truncated:
            _context.set_item("subprocess_popen_truncated", "yes", span=span)

        if is_shell:
            _context.set_item("subprocess_popen_line", shellcmd.as_string(), span=span)
        else:
            _context.set_item("subprocess_popen_line", shellcmd.as_list(), span=span)

        _context.set_item("subprocess_popen_binary", shellcmd.binary, span=span)

        wrapped(*args, **kwargs)


@trace_utils.with_traced_module
def traced_subprocess_wait(module, pin, wrapped, instance, args, kwargs):
    with pin.tracer.trace("subprocess.Popen.wait", span_type=SpanTypes.SYSTEM) as span:
        span.set_tag_str("name", "command_execution")

        if _context.get_item("subprocess_popen_is_shell", span=span):
            span.set_tag_str("cmd.shell", _context.get_item("subprocess_popen_line", span=span))
        else:
            span.set_tag("cmd.exec", _context.get_item("subprocess_popen_line", span=span))

        truncated = _context.get_item("subprocess_popen_truncated", span=span)
        if truncated:
            span.set_tag_str("cmd.truncated", "yes")
        span.set_tag_str("component", "subprocess")
        span.set_tag_str("resource", _context.get_item("subprocess_popen_binary", span=span))
        ret = wrapped(*args, **kwargs)
        span.set_tag_str("cmd.exit_code", str(ret))
        return ret
