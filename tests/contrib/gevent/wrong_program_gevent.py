from gevent import monkey

import ddtrace  # noqa


monkey.patch_all()
