import ddtrace.profiling.auto  # noqa

from gevent import monkey

monkey.patch_all()
