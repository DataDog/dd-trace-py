import os
import logging

from ddtrace.internal import periodic
from ddtrace.internal.utils.time import StopWatch

from ddtrace.remoteconfig._client import Client

log = logging.getLogger(__name__)


DEFAULT_REMOTECONFIG_POLL_SECONDS = 30


def get_poll_interval_seconds():
    # type:() -> int
    return int(os.getenv("DD_REMOTECONFIG_POLL_SECONDS", default=DEFAULT_REMOTECONFIG_POLL_SECONDS))


class RemoteConfigWorker(periodic.PeriodicService):
    def __init__(self, poll_interval=get_poll_interval_seconds()):
        super(RemoteConfigWorker, self).__init__(interval=poll_interval)
        self._client = Client()

    def periodic(self):
        # type: () -> None
        with StopWatch() as sw:
            self._client.request()

        t = sw.elapsed()
        if t >= self.interval:
            log_level = logging.WARNING
        else:
            log_level = logging.DEBUG
        log.log(log_level, "request config in %.5fs to %s", t, self._client.agent_url)


if __name__ == "__main__":
    import time

    def debug(metadata, config):
        print("hello {!r}".format(metadata))

    logging.basicConfig(level=logging.DEBUG)
    c = RemoteConfigWorker(poll_interval=5)
    c._client.register_product("ASM_DD", debug)
    c._client.register_product("FEATURES", debug)
    c.start()
    time.sleep(360)
