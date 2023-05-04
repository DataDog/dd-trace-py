import collections
import re
import shlex
import subprocess
from fnmatch import fnmatch
from typing import Union

from ddtrace.internal import _context
from ddtrace import config

from ddtrace.ext import SpanTypes
from ddtrace.internal.logger import get_logger
from ddtrace.internal.compat import PY2

from ddtrace import Pin

from ddtrace.contrib import trace_utils
import os

log = get_logger(__name__)


"""
JJJ TODO:
- cmd.exec debe ser un array!
- Truncation
- flask snapshot tests from views
- exception handlers so it never fails and always executes the command
- constants for the tag names
"""



def _patch():
    # type: () -> None
    if not config._appsec_enabled:
        return

    import os
    Pin().onto(os)
    trace_utils.wrap(os, "system", traced_ossystem(os))
    # note: popen* uses subprocess, which we already wrap below

    # all os.spawn* variants eventually use this one:
    trace_utils.wrap(os, "_spawnvef", traced_osspawn(os))

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


_COMPILED_ENV_VAR_REGEXP = re.compile(r'\b[A-Z_]+=\w+')


class SubprocessCmdLine(object):
    ENV_VARS_ALLOWLIST = {
        'LD_PRELOAD',
        'LD_LIBRARY_PATH',
        'PATH'
    }

    BINARIES_DENYLIST = {
        'md5',
    }

    SENSITIVE_WORDS_WILDCARDS = (
        "*password*", "*passwd*", "*mysql_pwd*",
        "*access_token*", "*auth_token*",
        "*api_key*", "*apikey*",
        "*secret*", "*credentials*", "stripetoken"
    )

    def __init__(self, shell_args, as_list=False, shell=False):
        # type: (Union[str, list], bool, bool) -> None
        self.env_vars = []
        self.binary = ''
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


    def scrub_env_vars(self, tokens):
        for idx, token in enumerate(tokens):
            if re.match(_COMPILED_ENV_VAR_REGEXP, token):
                var, value = token.split('=')
                if var in self.ENV_VARS_ALLOWLIST:
                    self.env_vars.append(token)
                else:
                    # scrub the value
                    self.env_vars.append("%s=?" % var)
            else:
                # Next after vars are the binary and arguments
                try:
                    self.binary = tokens[idx]
                    self.arguments = tokens[idx + 1:]
                except IndexError:
                    pass
                break

    def scrub_arguments(self):
        # if the binary is in the denylist, scrub all arguments
        if self.binary.lower() in self.BINARIES_DENYLIST:
            self.arguments = ['?' for _ in self.arguments]
            return

        param_prefixes = ('-', '/')
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
                is_sensitive=False

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


    def as_list(self):
        return self.env_vars + [self.binary] + self.arguments

    def maybe_truncate_string(self, str):
        # type: (str) -> str


    def as_string(self):
        return shlex.join(self.as_list())


def scrub_arg(_arg):
    # type: (str) -> str
    # JJJ better scrubbing
    return _arg


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

        shellcmd = SubprocessCmdLine(func_args, True, shell=False)
        span.set_tag_str("cmd.exec", shellcmd.as_string())
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
def traced_subprocess_init(module, pin, wrapped, instance, args, kwargs):
    with pin.tracer.trace("subprocess.Popen.init", span_type=SpanTypes.SYSTEM) as span:
        cmd_args = args[0] if len(args) else kwargs["args"]
        cmd_args_list = shlex.split(cmd_args) if isinstance(cmd_args, str) else cmd_args
        is_shell = kwargs.get("shell", False)
        _context.set_item("subprocess_popen_is_shell", is_shell, span=span)

        shellcmd = SubprocessCmdLine(cmd_args_list, True, shell=is_shell)
        if shellcmd.truncated:
            _context.set_item("subprocess_popen_truncated", "yes", span=span)
        _context.set_item("subprocess_popen_line", shellcmd.as_string(), span=span)
        _context.set_item("subprocess_popen_binary", shellcmd.binary, span=span)

        wrapped(*args, **kwargs)


@trace_utils.with_traced_module
def traced_subprocess_wait(module, pin, wrapped, instance, args, kwargs):
    with pin.tracer.trace("subprocess.Popen.wait", span_type=SpanTypes.SYSTEM) as span:
        span.set_tag_str("name", "command_execution")

        sh_tag = "cmd.shell" if _context.get_item("subprocess_popen_is_shell", span=span) else "cmd.exec"
        span.set_tag_str(sh_tag, _context.get_item("subprocess_popen_line", span=span))
        truncated = _context.get_item("subprocess_popen_truncated", span=span)
        if truncated:
            span.set_tag_str("cmd.truncated", "yes")
        span.set_tag_str("component", "subprocess")
        span.set_tag_str("resource", _context.get_item("subprocess_popen_binary", span=span))
        ret = wrapped(*args, **kwargs)
        span.set_tag_str("cmd.exit_code", str(ret))
        return ret

