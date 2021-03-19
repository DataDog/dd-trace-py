"""
ddtrace.vendor
==============
Install vendored dependencies under a different top level package to avoid importing `ddtrace/__init__.py`
whenever a dependency is imported. Doing this allows us to have a little more control over import order.


Dependencies
============

six
---

Website: https://six.readthedocs.io/
Source: https://github.com/benjaminp/six
Version: 1.14.0
License: MIT

Notes:
  `six/__init__.py` is just the source code's `six.py`


wrapt
-----

Website: https://wrapt.readthedocs.io/en/latest/
Source: https://github.com/GrahamDumpleton/wrapt/
Version: 1.12.1
License: BSD 2-Clause "Simplified" License

Notes:
  `setup.py` will attempt to build the `wrapt/_wrappers.c` C module


dogstatsd
---------

Website: https://datadogpy.readthedocs.io/en/latest/
Source: https://github.com/DataDog/datadogpy
Version: 8e11af2 (0.39.1)
License: Copyright (c) 2020, Datadog <info@datadoghq.com>

Notes:
  `dogstatsd/__init__.py` was updated to include a copy of the `datadogpy` license: https://github.com/DataDog/datadogpy/blob/master/LICENSE
  Only `datadog.dogstatsd` module was vendored to avoid unnecessary dependencies
  `datadog/util/compat.py` was copied to `dogstatsd/compat.py`
  `datadog/util/format.py` was copied to `dogstatsd/format.py`
  version fixed to 8e11af2
  removed type imports
  removed unnecessary compat utils


monotonic
---------

Website: https://pypi.org/project/monotonic/
Source: https://github.com/atdt/monotonic
Version: 1.5
License: Apache License 2.0

Notes:
  The source `monotonic.py` was added as `monotonic/__init__.py`

  No other changes were made

debtcollector
-------------

Website: https://docs.openstack.org/debtcollector/latest/index.html
Source: https://github.com/openstack/debtcollector
Version: 1.22.0
License: Apache License 2.0

Notes:
   Removed dependency on `pbr` and manually set `__version__`


psutil
------

Website: https://github.com/giampaolo/psutil
Source: https://github.com/giampaolo/psutil
Version: 5.6.7
License: BSD 3

attrs
-----

Website: http://www.attrs.org/
Source: https://github.com/python-attrs/attrs
Version: 20.3.0
License: MIT


contextvars
-------------

Source: https://github.com/MagicStack/contextvars
Version: 2.4
License: Apache License 2.0

Notes:
  - removal of metaclass usage
  - formatting
  - use a plain old dict instead of immutables.Map
  - removal of `*` syntax
"""

# Initialize `ddtrace.vendor.datadog.base.log` logger with our custom rate limited logger
# DEV: This helps ensure if there are connection issues we do not spam their logs
# DEV: Overwrite `base.log` instead of `get_logger('datadog.dogstatsd')` so we do
#      not conflict with any non-vendored datadog.dogstatsd logger
from ..internal.logger import get_logger
from .dogstatsd import base
base.log = get_logger('ddtrace.vendor.dogstatsd')
