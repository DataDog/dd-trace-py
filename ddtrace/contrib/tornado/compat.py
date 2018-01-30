from ..util import require_modules


optional_modules = ['concurrent.futures']

with require_modules(optional_modules) as missing_modules:
    # detect if concurrent.futures is available as a Python
    # stdlib or Python 2.7 backport
    futures_available = len(missing_modules) == 0
