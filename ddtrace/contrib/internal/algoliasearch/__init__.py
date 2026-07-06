"""
The Algoliasearch__ integration will add tracing to your Algolia searches.

::

    import ddtrace.auto

    # algoliasearch < 4
    from algoliasearch import algoliasearch
    client = algoliasearch.Client(<ID>, <API_KEY>)
    index = client.init_index(<INDEX_NAME>)
    index.search("your query", args={"attributesToRetrieve": "attribute1,attribute1"})

    # algoliasearch >= 4
    from algoliasearch.search.client import SearchClientSync
    client = SearchClientSync(<ID>, <API_KEY>)
    client.search_single_index(<INDEX_NAME>, search_params={"query": "your query"})

Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.algoliasearch['collect_query_text']

   Whether to pass the text of your query onto Datadog. Since this may contain sensitive data it's off by default

   Default: ``False``

.. __: https://www.algolia.com
"""
