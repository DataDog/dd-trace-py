"""
The Algoliasearch__ integration will add tracing to your Algolia searches.

::

    import ddtrace.auto

    from algoliasearch import algoliasearch
    client = alogliasearch.Client(<ID>, <API_KEY>)
    index = client.init_index(<INDEX_NAME>)
    index.search("your query", args={"attributesToRetrieve": "attribute1,attribute1"})

Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.algoliasearch['collect_query_text']

   Whether to pass the text of your query onto Datadog. Since this may contain sensitive data it's off by default

   Default: ``False``

.. __: https://www.algolia.com
"""


# Required to allow users to import from  `ddtrace.contrib.algoliasearch.patch` directly
import warnings as _w


with _w.catch_warnings():
    _w.simplefilter("ignore", DeprecationWarning)
    from . import patch as _  # noqa: F401, I001

from ddtrace.contrib.internal.algoliasearch.patch import get_version  # noqa: F401
from ddtrace.contrib.internal.algoliasearch.patch import patch  # noqa: F401
from ddtrace.contrib.internal.algoliasearch.patch import unpatch  # noqa: F401
