import random
import re
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
    if ddtrace.config._extra_services_queue is None:
        import ddtrace.internal._file_queue as file_queue

        ddtrace.config._extra_services_queue = file_queue.File_Queue()

    threads = [threading.Thread(target=write_in_subprocess, args=(i,)) for i in range(nb_service)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    extra_services = ddtrace.config._get_extra_services()
    assert len(extra_services) == min(nb_service, MAX_NAMES)
    assert all(re.match(r"extra_service_\d+", service) for service in extra_services)

    ddtrace.config._remote_config_enabled = default_remote_config_enabled
    if not default_remote_config_enabled:
        ddtrace.config._extra_services_queue = None
    ddtrace.config._extra_services = set()
