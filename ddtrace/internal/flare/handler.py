from ddtrace.internal.flare.flare import Flare
from ddtrace.internal.flare.flare import FlareSendRequest
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig import PayloadType


log = get_logger(__name__)


def cleanup_tracer_flare(flare: Flare) -> None:
    flare.revert_configs()
    flare.clean_up_files()


def prepare_tracer_flare(flare: Flare, configs: list[PayloadType]) -> bool:
    """
    Update configurations to start sending tracer logs to a file
    to be sent in a flare later.
    """
    for c in configs:
        try:
            if c is None:
                continue
            config_content = c.get("config", {})
            log_level = config_content.get("log_level")
            if not log_level:
                continue

            # Convert to uppercase to match Python logging expectations
            flare_log_level = log_level.upper()

            flare.prepare(flare_log_level)
            return True
        except Exception as e:
            log.warning("Remote config flare preparation failed: %s; config=%r", e, c)
    log.debug("Remote config flare preparation: config has no log level, skipping configs=%r", configs)
    return False


def generate_tracer_flare(flare: Flare, configs: list[PayloadType]) -> bool:
    """
    Revert tracer flare configurations back to original state
    before sending the flare.
    """
    for c in configs:
        # AGENT_TASK is currently being used for multiple purposes
        try:
            if c is None:
                continue
            if c.get("task_type") != "tracer_flare":
                # Ex: "task_type" can be "flare".
                continue
            args = c.get("args", {})
            uuid = c.get("uuid")
            if not uuid:
                log.warning("Remote config: flare upload request missing required uuid, skipping config=%r", c)
                continue

            flare_request = FlareSendRequest(
                case_id=args.get("case_id"), hostname=args.get("hostname"), email=args.get("user_handle"), uuid=uuid
            )

            flare.revert_configs()

            flare.send(flare_request)
            return True
        except Exception as e:
            log.warning("Remote config flare generation failed. %s; config=%r", e, c)
    log.debug("Remote config flare generation: no valid flare upload request in configs, skipping configs=%r", configs)
    return False
