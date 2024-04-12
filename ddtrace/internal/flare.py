import io
import json
import logging
import os
import secrets
import shutil
import tarfile
from typing import Any
from typing import Dict
from typing import List
from typing import Tuple

from ddtrace import config
from ddtrace._logger import _add_file_handler
from ddtrace.internal.logger import _get_handler
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.http import get_connection


TRACER_FLARE_DIRECTORY = "tracer_flare"
TRACER_FLARE_TAR = f"{TRACER_FLARE_DIRECTORY}.tar"
TRACER_FLARE_ENDPOINT = "/tracer_flare/v1"
TRACER_FLARE_FILE_HANDLER_NAME = "tracer_flare_file_handler"
TRACER_FLARE_LOCK = "tracer_flare.lock"

log = get_logger(__name__)


class Flare:
    def __init__(self):
        self.original_log_level = 0

    def _prepare(self, configs: List[dict]):
        """
        Update configurations to start sending tracer logs to a file
        to be sent in a flare later.
        """
        os.makedirs(TRACER_FLARE_DIRECTORY, exist_ok=True)
        for c in configs:
            # AGENT_CONFIG is currently being used for multiple purposes
            # We only want to prepare for a tracer flare if the config name
            # starts with 'flare-log-level'
            if not c.get("name", "").startswith("flare-log-level"):
                return

            # Validate the flare log level
            flare_log_level = c.get("config", {}).get("log_level").upper()
            flare_log_level_int = logging.getLevelName(flare_log_level)
            if type(flare_log_level_int) != int:
                raise TypeError("Invalid log level provided: %s", flare_log_level_int)

            ddlogger = get_logger("ddtrace")
            pid = os.getpid()
            flare_file_path = f"{TRACER_FLARE_DIRECTORY}/tracer_python_{pid}.log"
            self.original_log_level = ddlogger.level

            # Set the logger level to the more verbose between original and flare
            # We do this valid_original_level check because if the log level is NOTSET, the value is 0
            # which is the minimum value. In this case, we just want to use the flare level, but still
            # retain the original state as NOTSET/0
            valid_original_level = 100 if self.original_log_level == 0 else self.original_log_level
            logger_level = min(valid_original_level, flare_log_level_int)
            ddlogger.setLevel(logger_level)
            _add_file_handler(ddlogger, flare_file_path, flare_log_level, TRACER_FLARE_FILE_HANDLER_NAME)

            # Create and add config file
            self._generate_config_file(pid)

    def _send(self, configs: List[Any]):
        """
        Revert tracer flare configurations back to original state
        before sending the flare.
        """
        for c in configs:
            # AGENT_TASK is currently being used for multiple purposes
            # We only want to generate the tracer flare if the task_type is
            # 'tracer_flare'
            if type(c) != dict or c.get("task_type") != "tracer_flare":
                continue
            args = c.get("args", {})

            self._revert_configs()

            # We only want the flare to be sent once, even if there are
            # multiple tracer instances
            if not os.path.exists(TRACER_FLARE_LOCK):
                open(TRACER_FLARE_LOCK, "w").close()
                data = {
                    "case_id": args.get("case_id"),
                    "source": "tracer_python",
                    "hostname": args.get("hostname"),
                    "email": args.get("user_handle"),
                }
                try:
                    client = get_connection(config._trace_agent_url, timeout=5)
                    headers, body = self._generate_payload(data)
                    client.request("POST", TRACER_FLARE_ENDPOINT, body, headers)
                    response = client.getresponse()
                    if response.status == 200:
                        log.info("Successfully sent the flare")
                    else:
                        print("error uploading")
                        log.error("Upload failed with status code %s: %s", response.status, response.reason)
                except Exception as e:
                    raise Exception("Failed to send tracer flare: %s" % e)
                finally:
                    client.close()
                    # Clean up files regardless of success/failure
                    self._clean_up_files()

    def _generate_config_file(self, pid: int):
        config_file = f"{TRACER_FLARE_DIRECTORY}/tracer_config_{pid}.json"
        try:
            with open(config_file, "w") as f:
                tracer_configs = {
                    "configs": config.__dict__,
                }
                json.dump(
                    tracer_configs,
                    f,
                    default=lambda obj: obj.__repr__() if hasattr(obj, "__repr__") else obj.__dict__,
                    indent=4,
                )
        except Exception as e:
            log.warning("Failed to generate %s: %s", config_file, e)
            if os.path.exists(config_file):
                os.remove(config_file)

    def _revert_configs(self):
        ddlogger = get_logger("ddtrace")
        handler_to_remove = _get_handler(ddlogger, TRACER_FLARE_FILE_HANDLER_NAME)
        if handler_to_remove:
            ddlogger.removeHandler(handler_to_remove)
            log.debug("ddtrace logs will not be routed to the %s file handler anymore", TRACER_FLARE_FILE_HANDLER_NAME)
        else:
            log.debug("Could not find %s to remove", TRACER_FLARE_FILE_HANDLER_NAME)
        ddlogger.setLevel(self.original_log_level)

    def _generate_payload(self, params: Dict[str, str]) -> Tuple[dict, bytes]:
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            for file_name in os.listdir(TRACER_FLARE_DIRECTORY):
                flare_file_name = f"{TRACER_FLARE_DIRECTORY}/{file_name}"
                tar.add(flare_file_name)
        tar_stream.seek(0)

        newline = "\r\n"
        boundary = secrets.token_hex(16)
        body = io.StringIO()
        for key, value in params.items():
            body.write("--" + boundary + newline)
            body.write(f'Content-Disposition: form-data; name="{key}"{newline}{newline}')
            body.write(f"{value}{newline}")

        body.write("--" + boundary + newline)
        body.write(f'Content-Disposition: form-data; name="flare_file"; filename="flare.tar"{newline}')
        body.write(f"Content-Type: application/octet-stream{newline}{newline}")
        body.write(tar_stream.getvalue().decode() + newline)
        body.write("--" + boundary + "--")
        headers = {
            "Content-Type": "multipart/form-data; boundary=" + boundary,
            "Content-Length": str(len(body.getvalue().encode())),
        }
        if config._dd_api_key:
            headers["DD-API-KEY"] = config._dd_api_key
        return headers, body.getvalue().encode()

    def _get_valid_logger_level(self, flare_log_level: int) -> int:
        valid_original_level = 100 if self.original_log_level == 0 else self.original_log_level
        return min(valid_original_level, flare_log_level)

    def _clean_up_files(self):
        try:
            shutil.rmtree(TRACER_FLARE_DIRECTORY)
        except Exception as e:
            log.warning("Failed to clean up tracer flare files: %s", e)

        try:
            os.remove(TRACER_FLARE_LOCK)
        except Exception as e:
            log.warning("Failed to clean up tracer lock file: %s", e)
