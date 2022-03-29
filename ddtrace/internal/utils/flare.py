# (C) Datadog, Inc. 2010-2016
# All rights reserved
# Licensed under Simplified BSD License (see LICENSE)
# reference: https://github.com/DataDog/dd-agent/blob/526559be731b6e47b12d7aa8b6d45cb8d9ac4d68/utils/flare.py

import atexit
import glob
import json
import logging
import os
from pathlib import Path
import re
import tarfile
from time import strftime

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
            # Open the tar file (context manager) and return it
            t.add(os.path.basename(file_name))


class Flare(object):
    # Single tar file used by all ddtrace flares
    _tar = TarFile()

    def collect(self):
        raise NotImplementedError

    def move_file_to_tar(self, file):
        self._tar.add_file(file.name)
        # close and remove file
        file.close()
        os.remove(file.name)

    @classmethod
    def get_tar_path(self):
        return self._tar.tar_path


class LogFlare(Flare):
    # Copy this from trace-agent flare
    LOGS_PATH = os.getenv("DD_TRACE_FLARE_LOG_PATH", default="PATH_WHICH_MATCHES_ALL_TRACER_LOGS")

    def __init__(self):
        super(Flare, self).__init__()
        atexit.register(self.collect)

    def collect(self):
        log.info("Collecting logs")
        for f in glob.glob(self.LOGS_PATH):
            if os.access(f, os.R_OK):
                self.move_file_to_tar(f)
        log.info("Saving all log files to %s", self.get_tar_path())


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
        atexit.register(self.collect)

    def collect(self):
        log.info("Collecting tracer configurations")
        f = self.create_flare_file()
        self.move_file_to_tar(f)
        log.info("Saving all log files to %s", self.get_tar_path())

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
