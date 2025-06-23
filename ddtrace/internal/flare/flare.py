import binascii
import dataclasses
import io
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import pathlib
import shutil
import time
from typing import Dict
from typing import Optional
from typing import Tuple
from urllib.parse import urlparse
import zipfile

from ddtrace._logger import _add_file_handler
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.http import get_connection


TRACER_FLARE_DIRECTORY = "tracer_flare"
TRACER_FLARE_ZIP = pathlib.Path("tracer_flare.zip")
TRACER_FLARE_ENDPOINT = "/tracer_flare/v1"
TRACER_FLARE_FILE_HANDLER_NAME = "tracer_flare_file_handler"
TRACER_FLARE_LOCK = pathlib.Path("tracer_flare.lock")
DEFAULT_TIMEOUT_SECONDS = 5

log = get_logger(__name__)


@dataclasses.dataclass
class FlareSendRequest:
    case_id: str
    hostname: str
    email: str
    uuid: str  # UUID from AGENT_TASK config for race condition prevention
    source: str = "tracer_python"


class TracerFlareSendError(Exception):
    pass


class Flare:
    def __init__(
        self,
        trace_agent_url: str,
        ddconfig: dict,
        api_key: Optional[str] = None,
        timeout_sec: int = DEFAULT_TIMEOUT_SECONDS,
        flare_dir: str = TRACER_FLARE_DIRECTORY,
    ):
        self.original_log_level: int = logging.NOTSET
        self.timeout: int = timeout_sec
        self.flare_dir: pathlib.Path = pathlib.Path(flare_dir)
        self.file_handler: Optional[RotatingFileHandler] = None
        self.url: str = trace_agent_url
        self._api_key: Optional[str] = api_key
        self.ddconfig = ddconfig

    def prepare(self, log_level: str):
        """
        Update configurations to start sending tracer logs to a file
        to be sent in a flare later.
        """
        try:
            self.flare_dir.mkdir(exist_ok=True)
        except Exception as e:
            log.error("Failed to create %s directory: %s", self.flare_dir, e)
            return

        flare_log_level_int = logging.getLevelName(log_level)
        if type(flare_log_level_int) != int:
            raise TypeError("Invalid log level provided: %s", log_level)

        ddlogger = get_logger("ddtrace")
        pid = os.getpid()
        flare_file_path = self.flare_dir / f"tracer_python_{pid}.log"
        self.original_log_level = ddlogger.level

        # Set the logger level to the more verbose between original and flare
        # We do this valid_original_level check because if the log level is NOTSET, the value is 0
        # which is the minimum value. In this case, we just want to use the flare level, but still
        # retain the original state as NOTSET/0
        valid_original_level = (
            logging.CRITICAL if self.original_log_level == logging.NOTSET else self.original_log_level
        )
        logger_level = min(valid_original_level, flare_log_level_int)
        ddlogger.setLevel(logger_level)
        self.file_handler = _add_file_handler(
            ddlogger, flare_file_path.__str__(), flare_log_level_int, TRACER_FLARE_FILE_HANDLER_NAME
        )

        # Create and add config file
        self._generate_config_file(pid)

    def send(self, flare_send_req: FlareSendRequest):
        """
        Revert tracer flare configurations back to original state
        before sending the flare.
        """
        self.revert_configs()

        # Ensure the flare directory exists (it might have been deleted by clean_up_files)
        try:
            self.flare_dir.mkdir(exist_ok=True)
        except Exception as e:
            log.error("Failed to create %s directory: %s", self.flare_dir, e)
            return

        # # Validate case_id (must be a digit and cannot be 0 according to spec)
        if flare_send_req.case_id in ("0", 0):
            log.warning("Case ID cannot be 0, skipping flare send")
            self.clean_up_files()
            return

        if not flare_send_req.case_id.isdigit():
            log.warning("Case ID string must contain a digit, skipping flare send")
            self.clean_up_files()
            return

        try:
            # We only want the flare to be sent once, even if there are
            # multiple tracer instances
            lock_path = self.flare_dir / TRACER_FLARE_LOCK
            if not os.path.exists(lock_path):
                try:
                    open(lock_path, "w").close()
                except Exception as e:
                    log.error("Failed to create %s file", lock_path)
                    raise e
                client = None
                try:
                    client = get_connection(self.url, timeout=self.timeout)
                    headers, body = self._generate_payload(flare_send_req)
                    client.request("POST", TRACER_FLARE_ENDPOINT, body, headers)
                    response = client.getresponse()
                    if response.status == 200:
                        log.info("Successfully sent the flare to Zendesk ticket %s", flare_send_req.case_id)
                    else:
                        msg = "Tracer flare upload responded with status code %s:(%s) %s" % (
                            response.status,
                            response.reason,
                            response.read().decode(),
                        )
                        raise TracerFlareSendError(msg)
                except Exception as e:
                    log.error("Failed to send tracer flare to Zendesk ticket %s: %s", flare_send_req.case_id, e)
                    raise e
                finally:
                    if client is not None:
                        client.close()
        finally:
            self.clean_up_files()

    def _generate_config_file(self, pid: int):
        config_file = self.flare_dir / f"tracer_config_{pid}.json"
        try:
            with open(config_file, "w") as f:
                # Redact API key if present
                api_key = self.ddconfig.get("_dd_api_key")
                if api_key:
                    self.ddconfig["_dd_api_key"] = "*" * (len(api_key) - 4) + api_key[-4:]

                tracer_configs = {
                    "configs": self.ddconfig,
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

    def revert_configs(self):
        ddlogger = get_logger("ddtrace")
        if self.file_handler:
            ddlogger.removeHandler(self.file_handler)
            log.debug("ddtrace logs will not be routed to the %s file handler anymore", TRACER_FLARE_FILE_HANDLER_NAME)
        else:
            log.debug("Could not find %s to remove", TRACER_FLARE_FILE_HANDLER_NAME)
        ddlogger.setLevel(self.original_log_level)

    def _generate_payload(self, flare_send_req):
        """
        Generate the multipart form-data payload for the flare request.
        """
        body = io.BytesIO()

        # Use a fixed boundary for consistency 
        boundary = "83CAD6AA-8A24-462C-8B3D-FF9CC683B51B"

        # Create the multipart form data in the same order:
        # source, case_id, hostname, email, uuid, flare_file

        # 1. source field
        body.write(f"--{boundary}\r\n".encode())
        body.write(b'Content-Disposition: form-data; name="source"\r\n\r\n')
        body.write(b'tracer_python\r\n')

        # 2. case_id field
        body.write(f"--{boundary}\r\n".encode())
        body.write(b'Content-Disposition: form-data; name="case_id"\r\n\r\n')
        body.write(f"{flare_send_req.case_id}\r\n".encode())

        # 3. hostname field
        body.write(f"--{boundary}\r\n".encode())
        body.write(b'Content-Disposition: form-data; name="hostname"\r\n\r\n')
        body.write(f"{flare_send_req.hostname}\r\n".encode())

        # 4. email field
        body.write(f"--{boundary}\r\n".encode())
        body.write(b'Content-Disposition: form-data; name="email"\r\n\r\n')
        body.write(f"{flare_send_req.email}\r\n".encode())

        # 5. uuid field (new, per spec)
        body.write(f"--{boundary}\r\n".encode())
        body.write(b'Content-Disposition: form-data; name="uuid"\r\n\r\n')
        body.write(f"{flare_send_req.uuid}\r\n".encode())

        # 6. flare_file field with descriptive filename 
        timestamp = int(time.time() * 1000) 
        filename = f"tracer-python-{flare_send_req.case_id}-{timestamp}-debug.zip"
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="flare_file"; filename="{filename}"\r\n'.encode())
        body.write(b'Content-Type: application/octet-stream\r\n\r\n')

        # Create the zip file content separately
        zip_stream = io.BytesIO()
        with zipfile.ZipFile(zip_stream, mode="w", compression=zipfile.ZIP_DEFLATED) as zipf:
            for flare_file_name in self.flare_dir.iterdir():
                zipf.write(flare_file_name, arcname=flare_file_name.name)
        zip_stream.seek(0)
        body.write(zip_stream.getvalue())
        body.write(b'\r\n')

        # End boundary
        body.write(f"--{boundary}--\r\n".encode())

        # Set headers
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(body.tell()),
        }

        # Don't send DD-API-KEY or Host Header - the agent should add it when forwarding to backend
        return headers, body.getvalue()

    def _get_valid_logger_level(self, flare_log_level: int) -> int:
        valid_original_level = 100 if self.original_log_level == 0 else self.original_log_level
        return min(valid_original_level, flare_log_level)

    def clean_up_files(self):
        try:
            shutil.rmtree(self.flare_dir)
        except Exception as e:
            log.warning("Failed to clean up tracer flare files: %s", e)
