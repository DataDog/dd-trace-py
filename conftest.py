import sys


collect_ignore = []
if sys.version_info[0:2] < (3, 5):
    collect_ignore.append('tests/contrib/aiobotocore/py35')
    collect_ignore.append('tests/contrib/aiopg/py35')
