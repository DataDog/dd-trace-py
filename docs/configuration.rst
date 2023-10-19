.. _Configuration:

===============
 Configuration
===============

`ddtrace` can be configured using environment variables.
Many :ref:`integrations` can also be configured using environment variables,
see specific integration documentation for more details.

The following environment variables for the tracer are supported:


.. ddtrace-configuration-options::
   DD_ENV:
     description: |
         Set an application's environment e.g. ``prod``, ``pre-prod``, ``staging``. Added in ``v0.36.0``. See `Unified Service Tagging`_ for more information.

   DD_SERVICE:
     default: (autodetected)
     description: |
         Set the service name to be used for this application. A default is
         provided for these integrations: :ref:`bottle`, :ref:`flask`, :ref:`grpc`,
         :ref:`pyramid`, :ref:`pylons`, :ref:`tornado`, :ref:`celery`, :ref:`django` and
         :ref:`falcon`. Added in ``v0.36.0``. See `Unified Service Tagging`_ for more information.

   DD_SERVICE_MAPPING:
     description: |
         Define service name mappings to allow renaming services in traces, e.g. ``postgres:postgresql,defaultdb:postgresql``.

   DD_TAGS:
     description: |
         Set global tags to be attached to every span. Value must be either comma or space separated. e.g. ``key1:value1,key2:value2`` or ``key1:value key2:value2``.
     version_added:
       v0.38.0: Comma separated support added
       v0.48.0: Space separated support added

   DD_VERSION:
     description: |
         Set an application's version in traces and logs e.g. ``1.2.3``,
         ``6c44da20``, ``2020.02.13``. Generally set along with ``DD_SERVICE``.

         See `Unified Service Tagging`_ for more information.
     version_added:
       v0.36.0:

   DD_SITE:
     default: datadoghq.com
     description: |
         Specify which site to use for uploading profiles. Set to
         ``datadoghq.eu`` to use EU site.

   DD_TRACE_ENABLED:
     type: Boolean
     default: True
     description: |
         Enable sending of spans to the Agent. Note that instrumentation will still be installed and spans will be
         generated.
     version_added:
       v0.41.0: |
           Formerly named ``DATADOG_TRACE_ENABLED``

   DD_TRACE_OTEL_ENABLED:
     type: Boolean
     default: False
     description: |
         When used with ``ddtrace-run`` this configuration enables OpenTelemetry support. To enable OpenTelemetry without `ddtrace-run` refer
         to the following :mod:`docs <ddtrace.opentelemetry>`.
     version_added:
       v1.12.0:

   DD_INSTRUMENTATION_TELEMETRY_ENABLED:
     type: Boolean
     default: True
     description: |
         Enables sending :ref:`telemetry <Instrumentation Telemetry>` events to the agent.

   DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED:
     type: Boolean
     default: False
     description: |
         This configuration enables the generation of 128 bit trace ids.
     version_added:
       v1.12.0:

   DD_TRACE_DEBUG:
     type: Boolean
     default: False
     description: |
         Enables debug logging in the tracer. Setting this flag will cause the library to create a root logging handler if one does not already exist.

         Can be used with `DD_TRACE_LOG_FILE` to route logs to a file.
     version_added:
       v0.41.0: |
           Formerly named ``DATADOG_TRACE_DEBUG``

   DD_TRACE_LOG_FILE_LEVEL:
     default: DEBUG
     description: |
         Configures the ``RotatingFileHandler`` used by the `ddtrace` logger to write logs to a file based on the level specified.
         Defaults to `DEBUG`, but will accept the values found in the standard **logging** library, such as WARNING, ERROR, and INFO,
         if further customization is needed. Files are not written to unless ``DD_TRACE_LOG_FILE`` has been defined.

   DD_TRACE_LOG_FILE:
     description: |
         Directs `ddtrace` logs to a specific file. Note: The default backup count is 1. For larger logs, use with ``DD_TRACE_LOG_FILE_SIZE_BYTES``.
         To fine tune the logging level, use with ``DD_TRACE_LOG_FILE_LEVEL``.

   DD_TRACE_LOG_FILE_SIZE_BYTES:
     type: Int
     default: 15728640
     description: |
         Max size for a file when used with `DD_TRACE_LOG_FILE`. When a log has exceeded this size, there will be one backup log file created.
         In total, the files will store ``2 * DD_TRACE_LOG_FILE_SIZE_BYTES`` worth of logs.

   DD_TRACE_<INTEGRATION>_ENABLED:
     type: Boolean
     default: True
     description: |
         Enables <INTEGRATION> to be patched. For example, ``DD_TRACE_DJANGO_ENABLED=false`` will disable the Django
         integration from being installed.
     version_added:
       v0.41.0:

   DD_PATCH_MODULES:
     description: |
         Override the modules patched for this execution of the program. Must be
         a list in the ``module1:boolean,module2:boolean`` format. For example,
         ``boto:true,redis:false``.
     version_added:
       v0.55.0: |
           Formerly named ``DATADOG_PATCH_MODULES``

   DD_LOGS_INJECTION:
     type: Boolean
     default: False
     description: Enables :ref:`Logs Injection`.

   DD_CALL_BASIC_CONFIG:
     type: Boolean
     default: False
     description: Controls whether ``logging.basicConfig`` is called in ``ddtrace-run`` or when debug mode is enabled.

   DD_AGENT_HOST:
     type: String
     default: |
        ``localhost``
     description: |
         The host name to use to connect the Datadog agent for traces. The host name
         can be IPv4, IPv6, or a domain name. If ``DD_TRACE_AGENT_URL`` is specified, the
         value of ``DD_AGENT_HOST`` is ignored.

         Example for IPv4: ``DD_AGENT_HOST=192.168.10.1``

         Example for IPv6: ``DD_AGENT_HOST=2001:db8:3333:4444:CCCC:DDDD:EEEE:FFFF``

         Example for domain name: ``DD_AGENT_HOST=host``
     version_added:
        v0.17.0:
        v1.7.0:

   DD_TRACE_AGENT_URL:
     type: URL
     default: |
         ``unix:///var/run/datadog/apm.socket`` if available
         otherwise ``http://localhost:8126``
     description: |
           The URL to use to connect the Datadog agent for traces. The url can start with
           ``http://`` to connect using HTTP or with ``unix://`` to use a Unix
           Domain Socket.

           Example for http url: ``DD_TRACE_AGENT_URL=http://localhost:8126``

           Example for UDS: ``DD_TRACE_AGENT_URL=unix:///var/run/datadog/apm.socket``

   DD_DOGSTATSD_URL:
     type: URL
     default: |
         ``unix:///var/run/datadog/dsd.socket`` if available
         otherwise ``udp://localhost:8125``
     description: |
         The URL to use to connect the Datadog agent for Dogstatsd metrics. The url can start with
         ``udp://`` to connect using UDP or with ``unix://`` to use a Unix
         Domain Socket.

         Example for UDP url: ``DD_TRACE_AGENT_URL=udp://localhost:8125``

         Example for UDS: ``DD_TRACE_AGENT_URL=unix:///var/run/datadog/dsd.socket``

   DD_TRACE_AGENT_TIMEOUT_SECONDS:
     type: Float
     default: 2.0
     description: The timeout in float to use to connect to the Datadog agent.

   DD_TRACE_WRITER_BUFFER_SIZE_BYTES:
     type: Int
     default: 8388608
     description: The max size in bytes of traces to buffer between flushes to the agent.

   DD_TRACE_WRITER_MAX_PAYLOAD_SIZE_BYTES:
     type: Int
     default: 8388608
     description: |
         The max size in bytes of each payload item sent to the trace agent. If the max payload size is greater than buffer size,
         then max size of each payload item will be the buffer size.

   DD_TRACE_WRITER_INTERVAL_SECONDS:
     type: Float
     default: 1.0
     description: The time between each flush of traces to the trace agent.

   DD_TRACE_STARTUP_LOGS:
     type: Boolean
     default: False
     description: Enable or disable start up diagnostic logging.

   DD_TRACE_SAMPLE_RATE:
     type: Float
     default: 1.0
     description: A float, f, 0.0 <= f <= 1.0. f*100% of traces will be sampled.

   DD_TRACE_RATE_LIMIT:
     type: int
     default: 100
     description: |
        Maximum number of traces per second to sample. Set a rate limit to avoid the ingestion volume overages in the case of traffic spikes.

     version_added:
        v0.33.0:

   DD_TRACE_SAMPLING_RULES:
     type: JSON array
     description: |
         A JSON array of objects. Each object must have a “sample_rate”, and the “name”, “service”, "resource", and "tags" fields are optional. The “sample_rate” value must be between 0.0 and 1.0 (inclusive).

         **Example:** ``DD_TRACE_SAMPLING_RULES='[{"sample_rate":0.5,"service":"my-service","resource":"my-url","tags":{"my-tag":"example"}}]'``

         **Note** that the JSON object must be included in single quotes (') to avoid problems with escaping of the double quote (") character.
     version_added:
       v1.19.0: added support for "resource"
       v1.20.0: added support for "tags"

   DD_SPAN_SAMPLING_RULES:
     type: string
     description: |
         A JSON array of objects. Each object must have a "name" and/or "service" field, while the "max_per_second" and "sample_rate" fields are optional.
         The "sample_rate" value must be between 0.0 and 1.0 (inclusive), and will default to 1.0 (100% sampled).
         The "max_per_second" value must be >= 0 and will default to no limit.
         The "service" and "name" fields can be glob patterns:
         "*" matches any substring, including the empty string,
         "?" matches exactly one of any character, and any other character matches exactly one of itself.

         **Example:** ``DD_SPAN_SAMPLING_RULES='[{"sample_rate":0.5,"service":"my-serv*","name":"flask.re?uest"}]'``

     version_added:
        v1.4.0:

   DD_SPAN_SAMPLING_RULES_FILE:
     type: string
     description: |
         A path to a JSON file containing span sampling rules organized as JSON array of objects.
         For the rules each object must have a "name" and/or "service" field, and the "sample_rate" field is optional.
         The "sample_rate" value must be between 0.0 and 1.0 (inclusive), and will default to 1.0 (100% sampled).
         The "max_per_second" value must be >= 0 and will default to no limit.
         The "service" and "name" fields are glob patterns, where "glob" means:
         "*" matches any substring, including the empty string,
         "?" matches exactly one of any character, and any other character matches exactly one of itself.

         **Example:** ``DD_SPAN_SAMPLING_RULES_FILE="data/span_sampling_rules.json"'``
         **Example File Contents:** ``[{"sample_rate":0.5,"service":"*-service","name":"my-name-????", "max_per_second":"20"}, {"service":"xy?","name":"a*c"}]``

     version_added:
        v1.4.0:

   DD_TRACE_HEADER_TAGS:
     description: |
         A map of case-insensitive header keys to tag names. Automatically applies matching header values as tags on root spans.

         For example, ``User-Agent:http.useragent,content-type:http.content_type``.

   DD_TRACE_API_VERSION:
     default: |
         ``v0.4``
     description: |
         The trace API version to use when sending traces to the Datadog agent.

         Currently, the supported versions are: ``v0.3``, ``v0.4`` and ``v0.5``.
     version_added:
       v0.56.0:
       v1.7.0: default changed to ``v0.5``.
       v1.19.1: default reverted to ``v0.4``.

   DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP:
     default: |
         ``'(?ix)(?:(?:"|%22)?)(?:(?:old[-_]?|new[-_]?)?p(?:ass)?w(?:or)?d(?:1|2)?|pass(?:[-_]?phrase)?|secret|(?:api[-_]?|private[-_]?|public[-_]?|access[-_]?|secret[-_]?)key(?:[-_]?id)?|token|consumer[-_]?(?:id|key|secret)|sign(?:ed|ature)?|auth(?:entication|orization)?)(?:(?:\\s|%20)*(?:=|%3D)[^&]+|(?:"|%22)(?:\\s|%20)*(?::|%3A)(?:\\s|%20)*(?:"|%22)(?:%2[^2]|%[^2]|[^"%])+(?:"|%22))|(?: bearer(?:\\s|%20)+[a-z0-9._\\-]+|token(?::|%3A)[a-z0-9]{13}|gh[opsu]_[0-9a-zA-Z]{36}|ey[I-L](?:[\\w=-]|%3D)+\\.ey[I-L](?:[\\w=-]|%3D)+(?:\\.(?:[\\w.+/=-]|%3D|%2F|%2B)+)?|-{5}BEGIN(?:[a-z\\s]|%20)+PRIVATE(?:\\s|%20)KEY-{5}[^\\-]+-{5}END(?:[a-z\\s]|%20)+PRIVATE(?:\\s|%20)KEY(?:-{5})?(?:\\n|%0A)?|(?:ssh-(?:rsa|dss)|ecdsa-[a-z0-9]+-[a-z0-9]+)(?:\\s|%20|%09)+(?:[a-z0-9/.+]|%2F|%5C|%2B){100,}(?:=|%3D)*(?:(?:\\s|%20|%09)+[a-z0-9._-]+)?)'``
     description: A regexp to redact sensitive query strings. Obfuscation disabled if set to empty string
     version_added:
       v1.19.0: |
           ``DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP`` replaces ``DD_TRACE_OBFUSCATION_QUERY_STRING_PATTERN`` which is deprecated
           and will be deleted in 2.0.0

   DD_TRACE_PROPAGATION_STYLE:
     default: |
         ``tracecontext,datadog``
     description: |
         Comma separated list of propagation styles used for extracting trace context from inbound request headers and injecting trace context into outbound request headers.

         Overridden by ``DD_TRACE_PROPAGATION_STYLE_EXTRACT`` for extraction.

         Overridden by ``DD_TRACE_PROPAGATION_STYLE_INJECT`` for injection.

         The supported values are ``datadog``, ``b3multi``, and ``b3 single header``, ``tracecontext``, and ``none``.

         When checking inbound request headers we will take the first valid trace context in the order provided.
         When ``none`` is the only propagator listed, propagation is disabled.

         All provided styles are injected into the headers of outbound requests.

         Example: ``DD_TRACE_PROPAGATION_STYLE="datadog,b3"`` to check for both ``x-datadog-*`` and ``x-b3-*``
         headers when parsing incoming request headers for a trace context. In addition, to inject both ``x-datadog-*`` and ``x-b3-*``
         headers into outbound requests.

     version_added:
       v1.7.0: The ``b3multi`` propagation style was added and ``b3`` was deprecated in favor it.
       v1.7.0: Added support for ``tracecontext`` W3C headers. Changed the default value to ``DD_TRACE_PROPAGATION_STYLE="tracecontext,datadog"``.

   DD_TRACE_PROPAGATION_STYLE_EXTRACT:
     default: |
         ``tracecontext,datadog``
     description: |
         Comma separated list of propagation styles used for extracting trace context from inbound request headers.

         Overrides ``DD_TRACE_PROPAGATION_STYLE`` for extraction propagation style.

         The supported values are ``datadog``, ``b3multi``, and ``b3 single header``, ``tracecontext``, and ``none``.

         When checking inbound request headers we will take the first valid trace context in the order provided.
         When ``none`` is the only propagator listed, extraction is disabled.

         Example: ``DD_TRACE_PROPAGATION_STYLE_EXTRACT="datadog,b3multi"`` to check for both ``x-datadog-*`` and ``x-b3-*``
         headers when parsing incoming request headers for a trace context.

     version_added:
       v1.7.0: The ``b3multi`` propagation style was added and ``b3`` was deprecated in favor it.

   DD_TRACE_PROPAGATION_STYLE_INJECT:
     default: |
         ``tracecontext,datadog``
     description: |
         Comma separated list of propagation styles used for injecting trace context into outbound request headers.

         Overrides ``DD_TRACE_PROPAGATION_STYLE`` for injection propagation style.

         The supported values are ``datadog``, ``b3multi``, and ``b3 single header``, ``tracecontext``, and ``none``.

         All provided styles are injected into the headers of outbound requests.
         When ``none`` is the only propagator listed, injection is disabled.

         Example: ``DD_TRACE_PROPAGATION_STYLE_INJECT="datadog,b3multi"`` to inject both ``x-datadog-*`` and ``x-b3-*``
         headers into outbound requests.

     version_added:
       v1.7.0: The ``b3multi`` propagation style was added and ``b3`` was deprecated in favor it.

   DD_TRACE_X_DATADOG_TAGS_MAX_LENGTH:
     type: Integer
     default: 512
     description: |
         The maximum length of ``x-datadog-tags`` header allowed in the Datadog propagation style.
         Must be a value between 0 to 512. If 0, propagation of ``x-datadog-tags`` is disabled.

   DD_TRACE_PARTIAL_FLUSH_ENABLED:
     type: Boolean
     default: True
     description: Prevents large payloads being sent to APM.

   DD_TRACE_PARTIAL_FLUSH_MIN_SPANS:
     type: Integer
     default: 500
     description: Maximum number of spans sent per trace per payload when ``DD_TRACE_PARTIAL_FLUSH_ENABLED=True``.

   DD_APPSEC_ENABLED:
     type: Boolean
     default: False
     description: Whether to enable AppSec monitoring.

   DD_APPSEC_RULES:
     type: String
     description: Path to a json file containing AppSec rules.

   DD_COMPILE_DEBUG:
     type: Boolean
     default: False
     description: Compile Cython extensions in RelWithDebInfo mode (with debug info, but no debug code or asserts)

   DD_APPSEC_OBFUSCATION_PARAMETER_KEY_REGEXP:
     default: |
       ``(?i)(?:p(?:ass)?w(?:or)?d|pass(?:_?phrase)?|secret|(?:api_?|private_?|public_?)key)|token|consumer_?(?:id|key|secret)|sign(?:ed|ature)|bearer|authorization``
     description: Sensitive parameter key regexp for obfuscation.

   DD_APPSEC_OBFUSCATION_PARAMETER_VALUE_REGEXP:
     default: |
         ``(?i)(?:p(?:ass)?w(?:or)?d|pass(?:_?phrase)?|secret|(?:api_?|private_?|public_?|access_?|secret_?)key(?:_?id)?|token|consumer_?(?:id|key|secret)|sign(?:ed|ature)?|auth(?:entication|orization)?)(?:\s*=[^;]|"\s*:\s*"[^"]+")|bearer\s+[a-z0-9\._\-]+|token:[a-z0-9]{13}|gh[opsu]_[0-9a-zA-Z]{36}|ey[I-L][\w=-]+\.ey[I-L][\w=-]+(?:\.[\w.+\/=-]+)?|[\-]{5}BEGIN[a-z\s]+PRIVATE\sKEY[\-]{5}[^\-]+[\-]{5}END[a-z\s]+PRIVATE\sKEY|ssh-rsa\s*[a-z0-9\/\.+]{100,}``
     description: Sensitive parameter value regexp for obfuscation.

   DD_SUBPROCESS_SENSITIVE_WILDCARDS:
     type: String
     description: |
         Add more possible matches to the internal list of subprocess execution argument scrubbing. Must be a comma-separated list and 
         each item can take `fnmatch` style wildcards, for example: ``*ssn*,*personalid*,*idcard*,*creditcard*``.

   DD_HTTP_CLIENT_TAG_QUERY_STRING:
     type: Boolean
     default: True
     description: Send query strings in http.url tag in http client integrations.

   DD_HTTP_SERVER_TAG_QUERY_STRING:
     type: Boolean
     default: True
     description: Send query strings in http.url tag in http server integrations.
    
   DD_TRACE_SPAN_AGGREGATOR_RLOCK:
     type: Boolean
     default: True
     description: Whether the ``SpanAggregator`` should use an RLock or a Lock.
     version_added:
       v1.16.2: added with default of False
       v1.19.0: default changed to True

   DD_IAST_ENABLED:
     type: Boolean
     default: False
     description: Whether to enable IAST.

   DD_IAST_MAX_CONCURRENT_REQUESTS:
     type: Integer
     default: 2
     description: Number of requests analyzed at the same time.

   DD_IAST_VULNERABILITIES_PER_REQUEST:
     type: Integer
     default: 2
     description: Number of vulnerabilities reported in each request.

   DD_IAST_WEAK_HASH_ALGORITHMS:
     type: String
     default: "MD5,SHA1"
     description: Weak hashing algorithms that should be reported, comma separated.

   DD_IAST_WEAK_CIPHER_ALGORITHMS:
     type: String
     default: "DES,Blowfish,RC2,RC4,IDEA"
     description: Weak cipher algorithms that should be reported, comma separated.

   DD_IAST_REDACTION_ENABLED:
     type: Boolean
     default: True
     description: |
        Replace potentially sensitive information in the vulnerability report, like passwords with ``*`` for non tainted strings and ``abcde...``
        for tainted ones. This will use the regular expressions of the two next settings to decide what to scrub.
     version_added:
        v1.17.0:

   DD_IAST_REDACTION_NAME_PATTERN:
     type: String
     default: |
       ``(?i)^.*(?:p(?:ass)?w(?:or)?d|pass(?:_?phrase)?|secret|(?:api_?|private_?|public_?|access_?|secret_?)key(?:_?id)?|token|consumer_?(?:id|key|secret)|sign(?:ed|ature)?|auth(?:entication|orization)?)``
     description: |
        Regular expression containing key or name style strings matched against vulnerability origin and evidence texts.
        If it matches, the scrubbing of the report will be enabled.
     version_added:
        v1.17.0:

   DD_IAST_REDACTION_VALUE_PATTERN:
     type: String
     default: |
       ``(?i)bearer\s+[a-z0-9\._\-]+|token:[a-z0-9]{13}|gh[opsu]_[0-9a-zA-Z]{36}|ey[I-L][\w=-]+\.ey[I-L][\w=-]+(\.[\w.+\/=-]+)?|[\-]{5}BEGIN[a-z\s]+PRIVATE\sKEY[\-]{5}[^\-]+[\-]{5}END[a-z\s]+PRIVATE\sKEY|ssh-rsa\s*[a-z0-9\/\.+]{100,}``
     description: |
        Regular expression containing value style strings matched against vulnerability origin and evidence texts.
        If it matches, the scrubbing of the report will be enabled.
     version_added:
        v1.17.0:

   DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE:
     type: String
     default: "auto"
     description: |
        Controls whether module cloning logic is executed by ``ddtrace-run``. Module cloning involves saving copies of dependency modules for internal use by ``ddtrace``
        that will be unaffected by future imports of and changes to those modules by application code. Valid values for this variable are ``1``, ``0``, and ``auto``. ``1`` tells
        ``ddtrace`` to run its module cloning logic unconditionally, ``0`` tells it not to run that logic, and ``auto`` tells it to run module cloning logic only if ``gevent``
        is accessible from the application's runtime.
     version_added:
        v1.9.0:

   DD_CIVISIBILITY_AGENTLESS_ENABLED:
     type: Boolean
     default: False
     description: |
        Configures the ``CIVisibility`` service to use a test-reporting ``CIVisibilityWriter``.
        This writer sends payloads for traces on which it's used to the intake endpoint for
        Datadog CI Visibility. If there is a reachable Datadog agent that supports proxying
        these requests, the writer will send its payloads to that agent instead.
     version_added:
        v1.12.0:

   DD_CIVISIBILITY_AGENTLESS_URL:
     type: String
     default: ""
     description: |
        Configures the ``CIVisibility`` service to send event payloads to the specified host. If unspecified, the host "https://citestcycle-intake.<DD_SITE>"
        is used, where ``<DD_SITE>`` is replaced by that environment variable's value, or "datadoghq.com" if unspecified.
     version_added:
        v1.13.0:

   DD_CIVISIBILITY_ITR_ENABLED:
     type: Boolean
     default: False
     description: |
        Configures the ``CIVisibility`` service to generate and upload git packfiles in support
        of the Datadog Intelligent Test Runner. This configuration has no effect if ``DD_CIVISIBILITY_AGENTLESS_ENABLED`` is false.
     version_added:
        v1.13.0:

   DD_APPSEC_AUTOMATED_USER_EVENTS_TRACKING:
      type: String
      default: "safe"
      description: |
         Sets the mode for the automated user login events tracking feature which sets some traces on each user login event. The
         supported modes are ``safe`` which will only store the user id or primary key, ``extended`` which will also store
         the username, email and full name and ``disabled``. Note that this feature requires ``DD_APPSEC_ENABLED`` to be 
         set to ``true`` to work.  
      version_added:
         v1.15.0:

   DD_USER_MODEL_LOGIN_FIELD:
      type: String
      default: ""
      description: |
         Field to be used to read the user login when using a custom ``User`` model for the automatic login events. This field will take precedence over automatic inference.
         Please note that, if set, this field will be used to retrieve the user login even if ``DD_APPSEC_AUTOMATED_USER_EVENTS_TRACKING`` is set to ``safe`` and, 
         in some cases, the selected field could hold potentially private information.
      version_added:
         v1.15.0:

   DD_USER_MODEL_EMAIL_FIELD:
      type: String
      default: ""
      description: |
         Field to be used to read the user email when using a custom ``User`` model for the automatic login events. This field will take precedence over automatic inference.
      version_added:
         v1.15.0:

   DD_USER_MODEL_NAME_FIELD:
      type: String
      default: ""
      description: |
         Field to be used to read the user name when using a custom ``User`` model for the automatic login events. This field will take precedence over automatic inference.
      version_added:
         v1.15.0:



.. _Unified Service Tagging: https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging/


Profiling
---------

.. ddtrace-envier-configuration:: ddtrace.settings.profiling:ProfilingConfig
   :recursive: true


Dynamic Instrumentation
-----------------------

.. ddtrace-envier-configuration:: ddtrace.settings.dynamic_instrumentation:DynamicInstrumentationConfig


Exception Debugging
-------------------

.. ddtrace-envier-configuration:: ddtrace.settings.exception_debugging:ExceptionDebuggingConfig
