import os

from envier import En

from ddtrace.internal.utils.formats import asbool  # noqa:F401
from ddtrace.internal.utils.formats import parse_tags_str  # noqa:F401


requires = ["remote-configuration"]


class Config(En):
    __prefix__ = "dd.trace"

    enabled = En.v(bool, "enabled", default=True)


_config = Config()


def post_preload():
    if _config.enabled:
        from ddtrace._monkey import patch_all

        modules_to_patch = os.getenv("DD_PATCH_MODULES")
        modules_to_str = parse_tags_str(modules_to_patch)
        modules_to_bool = {k: asbool(v) for k, v in modules_to_str.items()}
        patch_all(**modules_to_bool)


def start():
    if _config.enabled:
        from ddtrace import config

        if config._trace_methods:
            from ddtrace.internal.tracemethods import _install_trace_methods  # noqa:F401

            _install_trace_methods(config._trace_methods)

    if "DD_TRACE_GLOBAL_TAGS" in os.environ:
        from ddtrace.trace import tracer

        env_tags = os.getenv("DD_TRACE_GLOBAL_TAGS")
        tracer.set_tags(parse_tags_str(env_tags))


def restart(join=False):
    from ddtrace.trace import tracer

    if tracer.enabled:
        tracer._child_after_fork()


def stop(join=False):
    from ddtrace.trace import tracer

    if tracer.enabled:
        tracer.shutdown()


def at_exit(join=False):
    from ddtrace.trace import tracer

    if tracer.enabled:
        tracer._atexit()
