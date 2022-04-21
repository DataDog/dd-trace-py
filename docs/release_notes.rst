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
    "1.1.0":
      ignore_notes:
        # Ignore 1.0 release notes
        - "1.0-remove-default-sampler-d1f6bcb2b23ca8a7.yaml"
        - "add-span-get-tags-metrics-7969ba7843dcc24d.yaml"
        - "add-warning-after-shutdown-tracer-dd8799467c4d29c8.yaml"
        - "aiohttp-98ae9ce70dda1dbc.yaml"
        - "aiohttp_jinja2-25d9a7b4e621fad2.yaml"
        - "asyncpg-45cdf83efdf9270d.yaml"
        - "ddtrace-1.0-disable-basic-config-call-by-default-b677844e4a13a794.yaml"
        - "deprecate-aiohttp_jinja2-patching-from-aiohttp-be87600f308ca87a.yaml"
        - "disable-internal-tag-propagation-dff3e799fb056584.yaml"
        - "encode-sns-msg-attributes-as-b64-7818aec10f533534.yaml"
        - "fix-aiohttp-jinja2-import-2b7e29a14a58efdc.yaml"
        - "fix-encode-tagset-value-2b8bb877a88bc75a.yaml"
        - "fix-psutil-macos-0cd7d0f93b34e3e4.yaml"
        - "internalize_http_server-06721b67e63dde1d.yaml"
        - "profiling-fix-memory-alloc-numbers-a280c751c8f250ba.yaml"
        - "pymongo-4.0.2-1f5d2b6af5c158d2.yaml"
        - "release-1.0-758fc917e40d7eb3.yaml"
        - "remove-cassandra-traced-1b3211d1f9071e6f.yaml"
        - "remove-celery-patch-task-3ad91299e26259d0.yaml"
        - "remove-clone-context-3cc6f3d73f15345e.yaml"
        - "remove-constants-deprecations-023fbd010e7c767c.yaml"
        - "remove-contrib-util-e4a33ee4684b457a.yaml"
        - "remove-ddtrace-compat-4146d5adc293bf17.yaml"
        - "remove-ddtrace-encoding-5e35232cc5b3f855.yaml"
        - "remove-ddtrace-http-affe61bc13f9c613.yaml"
        - "remove-ddtrace-install-excepthooks-8107b679a9f51ef3.yaml"
        - "remove-ddtrace-monkey-16e56038dc409769.yaml"
        - "remove-ddtrace-propagation-utils-171fa44d0479028f.yaml"
        - "remove-ddtrace-utils-ae7231d2e4f130b2.yaml"
        - "remove-deprecated-tracer-attributes-4bb24b794a1aacb9.yaml"
        - "remove-ext-errors-43d7aca3e1765807.yaml"
        - "remove-ext-priority-be59e69e52c65ec9.yaml"
        - "remove-ext-system-a70c1fbbec3b32ff.yaml"
        - "remove-helpers-f3c274e83104ff2a.yaml"
        - "remove-legacy-service-name-365da3952e073dd5.yaml"
        - "remove-mongoengine-traced-4e00b1175e6e5ca0.yaml"
        - "remove-mysql-legacy-c09206695dcaf541.yaml"
        - "remove-pin-app-apptype-8d0addd243deb7f9.yaml"
        - "remove-psycopg-factory-98769b2b2e040dd8.yaml"
        - "remove-requests-legacy-distributed-302154022c58186a.yaml"
        - "remove-span-deprecated-51fa2f55f53aebd5.yaml"
        - "remove-span-get-set-meta-c6fb2528d198414d.yaml"
        - "remove-span-todict-98117485031335cd.yaml"
        - "remove-span-tracer-ddtrace-1-0-bc0ced48b2806e3c.yaml"
        - "remove-sqlite3-connection-f641a48c5fa90e1f.yaml"
        - "remove-tracer-deprecations-c0abaecda9c24b79.yaml"
        - "remove-tracer-writer-b1875a1fa3230236.yaml"
        - "remove-v1deprecationwarning-2c251ca219182b97.yaml"
        - "remove_deprecated_environment_variables-3ed446513ab21409.yaml"
        - "removed_ddtrace.util.py-0d0f48aefa6d9779.yaml"
        - "span-types-enum-5ca7cb35031a199e.yaml"
        - "tracer-write-dc2f9d95c0e4d11f.yaml"
        - "update-deprecated-contrib-removal-version-ca920d9e1d91b6e0.yaml"
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
