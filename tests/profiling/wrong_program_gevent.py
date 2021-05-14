from gevent import monkey

import ddtrace.profiling.auto  # noqa


monkey.patch_all()
