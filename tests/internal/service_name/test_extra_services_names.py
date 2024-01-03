import random
import threading
import time

import pytest

import ddtrace


MAX_NAMES = 64


@pytest.mark.parametrize("nb_service", [2, 16, 64, 256])
def test_service_name(nb_service):
    ddtrace.config._extra_services = set()

    def write_in_subprocess(id_nb):
        time.sleep(random.random())
        ddtrace.config._add_extra_service(f"extra_service_{id_nb}")

    default_remote_config_enabled = ddtrace.config._remote_config_enabled
    ddtrace.config._remote_config_enabled = True

    threads = [threading.Thread(target=write_in_subprocess, args=(i,)) for i in range(nb_service)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    ddtrace.config._remote_config_enabled = default_remote_config_enabled
    assert len(ddtrace.config._get_extra_services()) == min(nb_service, MAX_NAMES)
    ddtrace.config._extra_services = set()
