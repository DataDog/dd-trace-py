"""
The Algoliasearch__ integration will add tracing to your Algolia searches.

::

    import ddtrace.auto

    from algoliasearch import algoliasearch
    client = alogliasearch.Client(<ID>, <API_KEY>)
    index = client.init_index(<INDEX_NAME>)
    index.search("your query", args={"attributesToRetrieve": "attribute1,attribute1"})

With ``algoliasearch`` 4.x, searches made through ``search_single_index`` on the sync and async
clients are traced::

    import ddtrace.auto

    from algoliasearch.search.client import SearchClientSync
    client = SearchClientSync(<ID>, <API_KEY>)
    client.search_single_index(<INDEX_NAME>, {"query": "your query", "hitsPerPage": 10})

Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.algoliasearch['collect_query_text']

   Whether to pass the text of your query onto Datadog. Since this may contain sensitive data it's off by default

   Default: ``False``

.. __: https://www.algolia.com
"""
