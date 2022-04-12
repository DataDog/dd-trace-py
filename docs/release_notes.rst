Release Notes
=============

..
    Load all release notes from the current branch when spell checking
    DEV: Without this we won't get spell checking on PRs or release
         notes that are not yet on a release branch.
    DEV: We generate the notes in a separate file to avoid any refs/directives
         colliding with the official notes. However, in order to get sphinx to
         not complain it must also exist in a toctree somewhere, so we add here
         hidden.

.. only:: spelling

    .. toctree::
        :hidden:

        _release_notes_all


.. ddtrace-release-notes::
    "1.0.0":
      ignore_notes:
        - "keep-alive-b5ec5febb435daad.yaml"
        - "aiohttp-98ae9ce70dda1dbc.yaml"
        - "deprecate-aiohttp_jinja2-patching-from-aiohttp-be87600f308ca87a.yaml"
        - "aiohttp_jinja2-25d9a7b4e621fad2.yaml"
        - "asyncpg-45cdf83efdf9270d.yaml"
        - "encode-sns-msg-attributes-as-b64-7818aec10f533534.yaml"
        - "fix-aiohttp-jinja2-import-2b7e29a14a58efdc.yaml"
        - "fix-encode-tagset-value-2b8bb877a88bc75a.yaml"
        - "fix-psutil-macos-0cd7d0f93b34e3e4.yaml"
        - "profiling-fix-memory-alloc-numbers-a280c751c8f250ba.yaml"
        - "pymongo-4.0.2-1f5d2b6af5c158d2.yaml"
        - "disable-internal-tag-propagation-dff3e799fb056584.yaml"
        - "add-span-get-tags-metrics-7969ba7843dcc24d.yaml"


Prior Releases
--------------
Release notes prior to v0.44.0 can be found in `CHANGELOG.md
<https://github.com/DataDog/dd-trace-py/blob/master/CHANGELOG.md>`_ in the root
of the repository.
