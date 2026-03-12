from ddtrace.internal.flare.flare import Flare
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def _handle_tracer_flare(flare: Flare, data: dict, cleanup: bool = False):
    if cleanup:
        log.info("Reverting tracer flare configurations and cleaning up any generated files")
        flare.revert_configs()
        flare.clean_up_files()
        return
