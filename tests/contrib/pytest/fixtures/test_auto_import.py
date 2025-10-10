"""Test script to verify ddtrace.auto behavior with pytest.ini."""

import os
import sys


sitecustomize_imported = "ddtrace.bootstrap.sitecustomize" in sys.modules

with open("auto_import_result.txt", "w") as f:
    f.write("SITECUSTOMIZE_IMPORTED={}".format(sitecustomize_imported))

print(f"SITECUSTOMIZE_IMPORTED={sitecustomize_imported}")
print(f"PYTHONPATH={os.environ.get('PYTHONPATH', '')}")
print(f"sys.path={sys.path}")
print(f"sys.modules keys: {list(k for k in sys.modules.keys() if 'ddtrace' in k or 'sitecustomize' in k)}")

assert not sitecustomize_imported, "sitecustomize was imported when it shouldn't have been"


def test_foo():
    assert 1 + 1 == 2
