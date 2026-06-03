import socket
import time

import pytest
import valkey

from tests.contrib.config import VALKEY_CLUSTER_CONFIG


@pytest.fixture(scope="session", autouse=True)
def wait_for_valkey_cluster():
    host = VALKEY_CLUSTER_CONFIG["host"]
    port = int(VALKEY_CLUSTER_CONFIG["ports"].split(",")[0])
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        if s.connect_ex((host, port)) != 0:
            return
    except Exception:
        return
    finally:
        s.close()
    timeout = 20
    client = valkey.Valkey(host=host, port=port)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            info = client.execute_command("CLUSTER INFO")
            if info.get("cluster_state") == "ok":
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("Valkey cluster at %s:%d not ready after %ds" % (host, port, timeout))
