import sys

import mock


if sys.version_info >= (3, 11, 0):
    sys.modules["bytecode"] = mock.MagicMock()
    import bytecode  # noqa
