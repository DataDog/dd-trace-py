import os
import runpy
import sys


if "DD_PROFILE_TEST_GEVENT" in os.environ:
    from gevent import monkey

    monkey.patch_all()
    print("=> gevent monkey patching done")

# TODO Use gevent.monkey once https://github.com/gevent/gevent/pull/1440 is merged?
module = sys.argv[1]
del sys.argv[0]
runpy.run_module(module, run_name="__main__")
