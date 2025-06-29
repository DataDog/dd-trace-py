from typing import Dict

from ddtrace.internal.logger import get_logger
from ddtrace.internal.wrapping.context import WrappingContext


log = get_logger(__name__)

DDTRACE_MODULE_NAME = "ddtrace"


class FreezegunConfigWrappingContext(WrappingContext):
    """Wraps the call to freezegun.configure to ensure that ddtrace remains patched if default_ignore_list is passed
    in as an argument
    """

    # __exit__ comes from the parent class
    # no-dd-sa:python-best-practices/ctx-manager-enter-exit-defined
    def __enter__(self) -> "FreezegunConfigWrappingContext":
        super().__enter__()
        try:
            default_ignore_list = self.get_local("default_ignore_list")
        except KeyError:
            log.debug("Could not get default_ignore_list on call to configure()")
            return

        if default_ignore_list is not None and DDTRACE_MODULE_NAME not in default_ignore_list:
            default_ignore_list.append(DDTRACE_MODULE_NAME)

        return self


def get_version() -> str:
    import freezegun

    try:
        return freezegun.__version__
    except AttributeError:
        log.debug("Could not get freezegun version")
        return ""


def _supported_versions() -> Dict[str, str]:
    return {"freezegun": "*"}


def patch() -> None:
    import freezegun

    if getattr(freezegun, "_datadog_patch", False):
        return

    FreezegunConfigWrappingContext(freezegun.configure).wrap()

    freezegun.configure(extend_ignore_list=[DDTRACE_MODULE_NAME])

    freezegun._datadog_patch = True


def unpatch() -> None:
    import freezegun

    if not getattr(freezegun, "_datadog_patch", False):
        return

    if FreezegunConfigWrappingContext.is_wrapped(freezegun.configure):
        FreezegunConfigWrappingContext.extract(freezegun.configure).unwrap()

    # Note: we do not want to restore to the original ignore list, as it may have been modified by the user, but we do
    # want to remove the ddtrace module from the ignore list
    new_ignore_list = [m for m in freezegun.config.settings.default_ignore_list if m != DDTRACE_MODULE_NAME]
    freezegun.configure(default_ignore_list=new_ignore_list)

    freezegun._datadog_patch = False
