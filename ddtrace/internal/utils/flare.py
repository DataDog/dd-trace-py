# (C) Datadog, Inc. 2010-2016
# All rights reserved
# Licensed under Simplified BSD License (see LICENSE)
# reference: https://github.com/DataDog/dd-agent/blob/526559be731b6e47b12d7aa8b6d45cb8d9ac4d68/utils/flare.py

import glob
import json
import logging
import os
from pathlib import Path
import re
import signal
import tarfile
from time import strftime
from typing import List

from ...internal.debug import collect as debug_collect


log = logging.getLogger(__name__)


class TarFile(object):
    """Prepares the tar file which will contain all flares"""

    DIR = os.getenv("DD_TRACE_FLARE_DIR", default="ddtrace_flare")

    def __init__(self):
        # Create directory if it does not exist
        Path(TarFile.DIR).mkdir(parents=True, exist_ok=True)

        self.file_name = "datadog-tracer-%s.tar.bz2" % strftime("%Y-%m-%d")
        self.tar_path = os.path.join(TarFile.DIR, self.file_name)
        # remove tar file if it exists
        if os.path.exists(self.tar_path):
            os.remove(self.tar_path)

    def add_file(self, file_name):
        with tarfile.open(self.tar_path, "w:bz2") as t:
            t.add(os.path.basename(file_name))
            log.info("File: %s added to tar: %s.", file_name, self.tar_path)

    def get_size(self):
        if os.path.exists(self.tar_path):
            return os.path.getsize(self.tar_path)
        return 0


class Flare(object):
    # Single tar file used by all ddtrace flares
    _tar = TarFile()
    # We limit to 10MB arbitrarily (just like the trace agent flare)
    MAX_SIZE = 10485000

    def collect(self):
        raise NotImplementedError

    @classmethod
    def get_tar_path(self):
        return self._tar.tar_path

    @classmethod
    def move_file_to_tar(self, file):
        if self.add_file_to_tar(file):
            # close and remove file
            file.close()
            os.remove(file.name)

    @classmethod
    def add_file_to_tar(self, file):
        if self._tar.get_size() + os.path.getsize(file.name) < self.MAX_SIZE:
            self._tar.add_file(file.name)
            return True
        log.info("Log file can not be added to ddtrace flare: %s.", file.name)
        return False


class FlareProcessor(object):
    def __init__(self, flares):
        # type: (List[Flare]) -> None
        self.flares = flares
        self.previous_handler = signal.getsignal(signal.SIGUSR2)
        signal.signal(signal.SIGUSR2, self.send_flares)

    def add_flare(self, flare):
        # type: (Flare) -> None
        self.flares.append(flare)

    def send_flares(self, signalNumber, frame):
        # So we don't overwrite pre-existing signal handlers
        self.previous_handler(signalNumber, frame)
        for flare in self.flares:
            flare.collect()


class LogFlare(Flare):
    # When this feature is shipped: https://github.com/DataDog/dd-trace-py/pull/3214,
    # we can could use DD_TRACE_LOG_FILE instead
    LOGS_PATH = os.getenv("DD_TRACE_FLARE_LOGS_TO_FLARE_REGEX", default="PATH_WHICH_MATCHES_ALL_TRACER_LOGS")

    def __init__(self):
        super(Flare, self).__init__()

    def collect(self):
        log.info("Collecting logs")
        for f in glob.glob(self.LOGS_PATH):
            self.add_file_to_tar(f)


class TracerFlare(Flare):
    FLARE_FILE = "tracer_flare.json"

    CONFIGURATIONS = {
        "DD_SERVICE",
        "DD_ENV",
        "DD_SERVICE_MAPPING",
        "DD_TAGS",
        "DD_VERSION",
        "DD_SITE",
        "DD_TRACE_ENABLED",
        "DD_PATCH_MODULES",
        "DD_TRACE_DEBUG",
        "DD_TRACE_AGENT_URL",
        "DD_DOGSTATSD_URL",
        "DD_TRACE_AGENT_TIMEOUT_SECONDS",
        "DD_TRACE_WRITER_BUFFER_SIZE_BYTES",
        "DD_TRACE_WRITER_MAX_PAYLOAD_SIZE_BYTES",
        "DD_TRACE_WRITER_INTERVAL_SECONDS",
        "DD_TRACE_STARTUP_LOGS",
        "DD_TRACE_SAMPLE_RATE",
        "DD_TRACE_SAMPLING_RULES",
        "DD_TRACE_HEADER_TAGS",
        "DD_TRACE_API_VERSION",
    }

    def __init__(self, tracer):
        self.tracer = tracer
        super(Flare, self).__init__()

    def collect(self):
        log.info("Collecting tracer configurations")
        f = self.create_flare_file()
        self.move_file_to_tar(f)

    def create_flare_file(self):
        tracer_configs = {
            "tracer": debug_collect(self.tracer),
            "configs": self.configs_dict(),
        }

        if os.path.exists(self.FLARE_FILE):
            os.remove(self.FLARE_FILE)
        with open(self.FLARE_FILE, "w") as f:
            json.dump(tracer_configs, f, indent=4)

        return f

    def configs_dict(self):
        configs = dict()
        for env in os.environ:
            val = os.environ[env]
            env_upper = env.upper()
            if env_upper in self.CONFIGURATIONS:
                configs[env_upper] = os.environ[env]
            elif re.search("DD_TRACE_.*_ENABLED", env_upper):
                configs[env_upper] = val
        return configs
