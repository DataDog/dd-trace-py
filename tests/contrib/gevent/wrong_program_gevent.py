from gevent import monkey

import ddtrace  # noqa:F401


monkey.patch_all()
