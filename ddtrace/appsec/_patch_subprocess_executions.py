import copy
import subprocess
from typing import Union

from ddtrace.internal import _context

from ddtrace.ext import SpanTypes
from ddtrace.internal.logger import get_logger
from ddtrace.internal.compat import PY2

from ddtrace import Pin

from ddtrace.contrib import trace_utils
import os

log = get_logger(__name__)


"""
XXX JJJ TODO:
- Truncation
- Param scrubbing
- cmd denylist
- env var parsing and allowlist
- shell command parsing
- unpatch
- flask snapshot tests from views
- exception handlers so it never fails
- constants for the tag names
"""

_PATCHED = {
    os: [
        {
            "name": "system",
            "shell_by_default": True,
            "shell_arg_name": None,
            "has_direct_return": True
        },
    ],
}


def _patch():
    import os
    Pin().onto(os)
    trace_utils.wrap(os, "system", traced_ossystem(os))
    trace_utils.wrap(os, "popen", traced_ospopen(os))
    # all os.spawn* variants eventually use this one:
    trace_utils.wrap(os, "_spawnvef", traced_osspawn(os))

    if PY2:
        trace_utils.wrap(os, "popen2", traced_ospopen(os))
        trace_utils.wrap(os, "popen3", traced_ospopen(os))
        trace_utils.wrap(os, "popen4", traced_ospopen(os))

    Pin().onto(subprocess)
    trace_utils.wrap(subprocess, "Popen.__init__", traced_subprocess_init(subprocess))
    trace_utils.wrap(subprocess, "Popen.wait", traced_subprocess_wait(subprocess))


def scrub_arg(_arg):
    # type: (str) -> str
    # XXX JJJ better scrubbing
    return _arg


def _scrub_params_maybe_truncate(_params):
    # type: (list[str]) -> tuple[str, list[str]]

    # XXX JJJ do truncation if needed!
    truncated = "false"

    return truncated, [scrub_arg(a) for a in _params]

def parse_shell_args(_args, as_list=False):
    # type: (Union[str, list], bool) -> tuple[str, list[str]]

    # XXX JJJ better parsing!
    tokens = _args.split(" ") if not as_list else _args
    cmd = tokens[0]
    truncated, params = _scrub_params_maybe_truncate(tokens[1:])
    return truncated, [cmd] + params


def parse_exec_args(_args_list):
    # type: (list[str]) -> tuple[str, list[str]]

    truncated, params = _scrub_params_maybe_truncate(_args_list)
    return truncated, params


@trace_utils.with_traced_module
def traced_ossystem(module, pin, wrapped, instance, args, kwargs):
    with pin.tracer.trace("os.system", span_type=SpanTypes.SYSTEM) as span:
        truncated, shell_tokens = parse_shell_args(args[0])
        span.set_tag_str("name", "command_execution")
        span.set_tag_str("cmd.shell", str(shell_tokens))
        span.set_tag_str("cmd.truncated", truncated)
        span.set_tag_str("component", "os")
        span.set_tag_str("resource", shell_tokens[0])
        ret = wrapped(*args, **kwargs)
        span.set_tag_str("cmd.exit_code", str(ret))
    return ret


@trace_utils.with_traced_module
def traced_ospopen(module, pin, wrapped, instance, args, kwargs):
    with pin.tracer.trace("os.popen", span_type=SpanTypes.SYSTEM) as span:
        truncated, shell_tokens = parse_shell_args(args[0])
        span.set_tag_str("name", "command_execution")
        span.set_tag_str("cmd.shell", str(shell_tokens))
        span.set_tag_str("cmd.truncated", truncated)
        span.set_tag_str("component", "os")
        span.set_tag_str("resource", shell_tokens[0])
        return wrapped(*args, **kwargs)


@trace_utils.with_traced_module
def traced_osspawn(module, pin, wrapped, instance, args, kwargs):
    with pin.tracer.trace("os.spawn", span_type=SpanTypes.SYSTEM) as span:
        span.set_tag_str("name", "command_execution")
        mode, file, func_args, _, _ = args

        truncated, exec_tokens = parse_exec_args(func_args)
        span.set_tag_str("cmd.exec", " ".join(exec_tokens))
        span.set_tag_str("cmd.truncated", truncated)
        span.set_tag_str("component", "os")
        span.set_tag_str("resource", file)

        if mode == os.P_WAIT:
            ret = wrapped(*args, **kwargs)
            span.set_tag_str("cmd.exit_code", str(ret))
            return ret

        return wrapped(*args, **kwargs)


@trace_utils.with_traced_module
def traced_subprocess_init(module, pin, wrapped, instance, args, kwargs):
    with pin.tracer.trace("subprocess.Popen.init", span_type=SpanTypes.SYSTEM) as span:
        cmd_args = args[0] if len(args) else kwargs["args"]
        is_shell = kwargs.get("shell", False)
        _context.set_item("subprocess_popen_is_shell", is_shell, span=span)

        if is_shell:
            truncated, tokens = parse_shell_args(" ".join(cmd_args))
        else:
            truncated, tokens = parse_exec_args(cmd_args)
        _context.set_item("subprocess_popen_truncated", truncated)
        _context.set_item("subprocess_popen_args", tokens)

        wrapped(*args, **kwargs)


@trace_utils.with_traced_module
def traced_subprocess_wait(module, pin, wrapped, instance, args, kwargs):
    with pin.tracer.trace("subprocess.Popen.wait", span_type=SpanTypes.SYSTEM) as span:
        span.set_tag_str("name", "command_execution")

        truncated, exec_tokens = parse_exec_args(instance.args)
        sh_tag = "cmd.shell" if _context.get_item("subprocess_popen_is_shell") else "cmd.exec"
        span.set_tag_str(sh_tag, " ".join(exec_tokens))
        span.set_tag_str("cmd.truncated", truncated)
        span.set_tag_str("component", "subprocess")
        span.set_tag_str("resource", exec_tokens[0])
        wrapped(*args, **kwargs)
        span.set_tag_str("cmd.exit_code", str(instance.returncode))

