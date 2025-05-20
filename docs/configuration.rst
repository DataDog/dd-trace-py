.. _Configuration:

===============
 Configuration
===============

`ddtrace` can be configured using environment variables.
Many :ref:`integrations` can also be configured using environment variables,
see specific integration documentation for more details.

The following environment variables for the tracer are supported:

Common Configurations
---------------------

For common configuration variables (not language specific), see `Configure the Datadog Tracing Library`_.


Unified Service Tagging
-----------------------

.. ddtrace-configuration-options::

   DD_ENV:
     description: |
         Set an application's environment e.g. ``prod``, ``pre-prod``, ``staging``. Added in ``v0.36.0``. See `Unified Service Tagging`_ for more information.

   DD_SERVICE:
     default: (autodetected)
     
     description: |
         Set the service name to be used for this application. A default is
         provided for these integrations: :ref:`bottle`, :ref:`flask`, :ref:`grpc`,
         :ref:`pyramid`, :ref:`tornado`, :ref:`celery`, :ref:`django` and
         :ref:`falcon`. Added in ``v0.36.0``. See `Unified Service Tagging`_ for more information.

   DD_VERSION:
     description: |
         Set an application's version in traces and logs e.g. ``1.2.3``,
         ``6c44da20``, ``2020.02.13``. Generally set along with ``DD_SERVICE``.

         See `Unified Service Tagging`_ for more information.
     
     version_added:
       v0.36.0:

Traces
------

.. ddtrace-configuration-options::
   DD_<INTEGRATION>_DISTRIBUTED_TRACING:
     default: True
     
     description: |
         Enables distributed tracing for the specified <INTEGRATION>.

     version_added:
       v2.7.0:

   DD_<INTEGRATION>_SERVICE:
      type: String
      default: <INTEGRATION>
      
      description: |
         Set the service name, allowing default service name overrides for traces for the specific <INTEGRATION>.
      
      version_added:
         v2.11.0:

   DD_ASGI_TRACE_WEBSOCKET:
     default: False
     
     description: |
         Enables tracing ASGI websockets. Please note that the websocket span duration will last until the 
         connection is closed, which can result in long running spans.

     version_added:
       v2.7.0:

   DD_BOTOCORE_EMPTY_POLL_ENABLED:
      type: Boolean
      default: True
      
      description: |
         Enables creation of consumer span when AWS SQS and AWS Kinesis ``poll()`` operations return no records. When disabled, no consumer span is created
         if no records are returned.
      
      version_added:
         v2.6.0:

   DD_BOTOCORE_PROPAGATION_ENABLED:
      type: Boolean
      default: False
      
      description: |
         Enables trace context propagation connecting producer and consumer spans within a single trace for AWS SQS, SNS, and Kinesis messaging services.
      
      version_added:
         v2.6.0:

   DD_HTTP_SERVER_TAG_QUERY_STRING:
     type: Boolean
     default: True
     description: Send query strings in http.url tag in http server integrations.

   DD_SERVICE_MAPPING:
     description: |
         Define service name mappings to allow renaming services in traces, e.g. ``postgres:postgresql,defaultdb:postgresql``.

   DD_TRACE_<INTEGRATION>_ENABLED:
     type: Boolean
     default: True
     
     description: |
         Enables <INTEGRATION> to be patched. For example, ``DD_TRACE_DJANGO_ENABLED=false`` will disable the Django
         integration from being installed.
     
     version_added:
       v0.41.0:

   DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED:
     type: Boolean
     default: True
     
     description: |
         This configuration enables the generation of 128 bit trace ids.
     
     version_added:
       v1.12.0:

   DD_TRACE_API_VERSION:
     default: |
         ``v0.5``
     
     description: |
         The trace API version to use when sending traces to the Datadog agent.

         Currently, the supported versions are: ``v0.4`` and ``v0.5``.
     
     version_added:
       v0.56.0:
       v1.7.0: default changed to ``v0.5``.
       v1.19.1: default reverted to ``v0.4``.
       v2.4.0: default changed to ``v0.5``.

   DD_TRACE_CLOUD_PAYLOAD_TAGGING_MAX_DEPTH:
      type: Integer
      default: 10
      description: |
         Sets the depth of expanding the JSON AWS payload after which we stop creating tags.
      version_added:
         v2.17.0:

   DD_TRACE_CLOUD_PAYLOAD_TAGGING_MAX_TAGS:
      type: Integer
      default: 758
      description: |
         Sets the the maximum number of tags that will be added when expanding an AWS payload.
      version_added:
         v2.17.0:

   DD_TRACE_CLOUD_PAYLOAD_TAGGING_SERVICES:
      type: Set
      default: {"s3", "sns", "sqs", "kinesis", "eventbridge"}
      description: |
         Sets the enabled AWS services to be expanded when AWS payload tagging is enabled.
      version_added:
         v2.17.0:

   DD_TRACE_CLOUD_REQUEST_PAYLOAD_TAGGING:
      type: String
      default: None
      description: |
         Enables AWS request payload tagging when set to ``"all"`` or a valid comma-separated list of ``JSONPath``\s.
      version_added:
         v2.17.0:

   DD_TRACE_CLOUD_RESPONSE_PAYLOAD_TAGGING:
      type: String
      default: None
      description: |
         Enables AWS response payload tagging when set to ``"all"`` or a valid comma-separated list of ``JSONPath``\s.
      version_added:
         v2.17.0:

   DD_TRACE_HEADER_TAGS:
     description: |
         A map of case-insensitive http headers to tag names. Automatically applies matching header values as tags on request and response spans. For example if
         ``DD_TRACE_HEADER_TAGS=User-Agent:http.useragent,content-type:http.content_type``. The value of the header will be stored in tags with the name ``http.useragent`` and ``http.content_type``.

         If a tag name is not supplied the header name will be used. For example if
         ``DD_TRACE_HEADER_TAGS=User-Agent,content-type``. The value of http header will be stored in tags with the names ``http.<response/request>.headers.user-agent`` and ``http.<response/request>.headers.content-type``.

   DD_TRACE_HTTP_CLIENT_TAG_QUERY_STRING:
     type: Boolean
     default: True
     description: Send query strings in http.url tag in http client integrations.

   DD_TRACE_HTTP_SERVER_ERROR_STATUSES:
     type: String
     default: "500-599"
     
     description: |
        Comma-separated list of HTTP status codes that should be considered errors when returned by an HTTP request.
        Multiple comma separated error ranges can be set (ex:  ``200,400-404,500-599``).
        The status codes are used to set the ``error`` field on the span.

   DD_TRACE_METHODS:
     type: String
     default: ""
     
     description: |
        Specify methods to trace. For example: ``mod.submod:method1,method2;mod.submod:Class.method1``.
        Note that this setting is only compatible with ``ddtrace-run``, and that it doesn't work for methods implemented
        by libraries for which there's an integration in ``ddtrace/contrib``.
     
     version_added:
       v2.1.0:

   DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP:
     default: |
         ``'(?ix)(?:(?:"|%22)?)(?:(?:old[-_]?|new[-_]?)?p(?:ass)?w(?:or)?d(?:1|2)?|pass(?:[-_]?phrase)?|secret|(?:api[-_]?|private[-_]?|public[-_]?|access[-_]?|secret[-_]?)key(?:[-_]?id)?|token|consumer[-_]?(?:id|key|secret)|sign(?:ed|ature)?|auth(?:entication|orization)?)(?:(?:\\s|%20)*(?:=|%3D)[^&]+|(?:"|%22)(?:\\s|%20)*(?::|%3A)(?:\\s|%20)*(?:"|%22)(?:%2[^2]|%[^2]|[^"%])+(?:"|%22))|(?: bearer(?:\\s|%20)+[a-z0-9._\\-]+|token(?::|%3A)[a-z0-9]{13}|gh[opsu]_[0-9a-zA-Z]{36}|ey[I-L](?:[\\w=-]|%3D)+\\.ey[I-L](?:[\\w=-]|%3D)+(?:\\.(?:[\\w.+/=-]|%3D|%2F|%2B)+)?|-{5}BEGIN(?:[a-z\\s]|%20)+PRIVATE(?:\\s|%20)KEY-{5}[^\\-]+-{5}END(?:[a-z\\s]|%20)+PRIVATE(?:\\s|%20)KEY(?:-{5})?(?:\\n|%0A)?|(?:ssh-(?:rsa|dss)|ecdsa-[a-z0-9]+-[a-z0-9]+)(?:\\s|%20|%09)+(?:[a-z0-9/.+]|%2F|%5C|%2B){100,}(?:=|%3D)*(?:(?:\\s|%20|%09)+[a-z0-9._-]+)?)'``
     
     description: A regexp to redact sensitive query strings. Obfuscation disabled if set to empty string
     
     version_added:
       v1.19.0: |
           ``DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP`` replaces ``DD_TRACE_OBFUSCATION_QUERY_STRING_PATTERN`` which is deprecated
           and will be deleted in 2.0.0

   DD_TRACE_OTEL_ENABLED:
     type: Boolean
     default: False
     
     description: |
         When used with ``ddtrace-run`` this configuration enables OpenTelemetry support. To enable OpenTelemetry without `ddtrace-run` refer
         to the following :mod:`docs <ddtrace.opentelemetry>`.
     
     version_added:
       v1.12.0:

   DD_TRACE_PARTIAL_FLUSH_ENABLED:
     type: Boolean
     default: True
     description: Prevents large payloads being sent to APM.

   DD_TRACE_PARTIAL_FLUSH_MIN_SPANS:
     type: Integer
     default: 300
     description: Maximum number of spans sent per trace per payload when ``DD_TRACE_PARTIAL_FLUSH_ENABLED=True``.

   DD_TRACE_PROPAGATION_EXTRACT_FIRST:
     type: Boolean
     default: False
     description: Whether the propagator stops after extracting the first header.
     
     version_added:
       v2.3.0:

   DD_TRACE_PROPAGATION_HTTP_BAGGAGE_ENABLED:
     type: Boolean
     default: False
     
     description: |
         Enables propagation of baggage items through http headers with prefix ``ot-baggage-``.
     
     version_added:
       v2.4.0:

   DD_TRACE_PROPAGATION_STYLE:
     default: |
         ``datadog,tracecontext,baggage``
     
     description: |
         Comma separated list of propagation styles used for extracting trace context from inbound request headers and injecting trace context into outbound request headers.

         Overridden by ``DD_TRACE_PROPAGATION_STYLE_EXTRACT`` for extraction.

         Overridden by ``DD_TRACE_PROPAGATION_STYLE_INJECT`` for injection.

         The supported values are ``datadog``, ``b3multi``, ``tracecontext``, ``baggage``, and ``none``.

         When checking inbound request headers we will take the first valid trace context in the order provided.
         When ``none`` is the only propagator listed, propagation is disabled.

         All provided styles are injected into the headers of outbound requests.

         Example: ``DD_TRACE_PROPAGATION_STYLE="datadog,b3"`` to check for both ``x-datadog-*`` and ``x-b3-*``
         headers when parsing incoming request headers for a trace context. In addition, to inject both ``x-datadog-*`` and ``x-b3-*``
         headers into outbound requests.

     version_added:
       v1.7.0: The ``b3multi`` propagation style was added and ``b3`` was deprecated in favor it.
       v1.7.0: Added support for ``tracecontext`` W3C headers. Changed the default value to ``DD_TRACE_PROPAGATION_STYLE="tracecontext,datadog"``.
       v2.6.0: Updated default value to ``datadog,tracecontext``.
       v2.16.0: Updated default value to ``datadog,tracecontex,baggage``.

   DD_TRACE_SPAN_TRACEBACK_MAX_SIZE:
      type: Integer
      default: 30
      
      description: |
         The maximum length of a traceback included in a span.
      
      version_added:
         v2.3.0:

   DD_TRACE_WRITER_BUFFER_SIZE_BYTES:
     type: Int
     default: 8388608
     description: The max size in bytes of traces to buffer between flushes to the agent.

   DD_TRACE_WRITER_INTERVAL_SECONDS:
     type: Float
     default: 1.0
     description: The time between each flush of traces to the trace agent.

   DD_TRACE_WRITER_MAX_PAYLOAD_SIZE_BYTES:
     type: Int
     default: 8388608
     
     description: |
         The max size in bytes of each payload item sent to the trace agent. If the max payload size is greater than buffer size,
         then max size of each payload item will be the buffer size.

   DD_TRACE_X_DATADOG_TAGS_MAX_LENGTH:
     type: Integer
     default: 512
     
     description: |
         The maximum length of ``x-datadog-tags`` header allowed in the Datadog propagation style.
         Must be a value between 0 to 512. If 0, propagation of ``x-datadog-tags`` is disabled.

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

Trace Context propagation
-------------------------

.. ddtrace-configuration-options::

   DD_TRACE_PROPAGATION_STYLE_EXTRACT:
     default: |
         ``datadog,tracecontext``
     
     description: |
         Comma separated list of propagation styles used for extracting trace context from inbound request headers.

         Overrides ``DD_TRACE_PROPAGATION_STYLE`` for extraction propagation style.

         The supported values are ``datadog``, ``b3multi``, ``b3``, ``tracecontext``, and ``none``.

         When checking inbound request headers we will take the first valid trace context in the order provided.
         When ``none`` is the only propagator listed, extraction is disabled.

         Example: ``DD_TRACE_PROPAGATION_STYLE_EXTRACT="datadog,b3multi"`` to check for both ``x-datadog-*`` and ``x-b3-*``
         headers when parsing incoming request headers for a trace context.

     version_added:
       v1.7.0: The ``b3multi`` propagation style was added and ``b3`` was deprecated in favor it.

   DD_TRACE_PROPAGATION_BEHAVIOR_EXTRACT:
     default: |
         ``continue``
     
     description: |
         String for how to handle incoming request headers that are extracted for propagation of trace info.

         The supported values are ``continue``, ``restart``, and ``ignore``.

         After extracting the headers for propagation, this configuration determines what is done with them.

         The default value is ``continue`` which always propagates valid headers.
         ``ignore`` ignores all incoming headers and ``restart`` turns the first extracted valid propagation header 
         into a span link and propagates baggage if present.

         Example: ``DD_TRACE_PROPAGATION_STYLE_EXTRACT="ignore"`` to ignore all incoming headers and to start a root span without a parent.

     version_added:
       v2.20.0:

   DD_TRACE_PROPAGATION_STYLE_INJECT:
     default: |
         ``tracecontext,datadog``
     
     description: |
         Comma separated list of propagation styles used for injecting trace context into outbound request headers.

         Overrides ``DD_TRACE_PROPAGATION_STYLE`` for injection propagation style.

         The supported values are ``datadog``, ``b3multi``,``b3``, ``tracecontext``, and ``none``.

         All provided styles are injected into the headers of outbound requests.
         When ``none`` is the only propagator listed, injection is disabled.

         Example: ``DD_TRACE_PROPAGATION_STYLE_INJECT="datadog,b3multi"`` to inject both ``x-datadog-*`` and ``x-b3-*``
         headers into outbound requests.

     version_added:
       v1.7.0: The ``b3multi`` propagation style was added and ``b3`` was deprecated in favor it.

AppSec
------

.. ddtrace-configuration-options::

   DD_APPSEC_AUTOMATED_USER_EVENTS_TRACKING:
      type: String
      default: "safe"
      
      description: |
         Sets the mode for the automated user login events tracking feature which sets some traces on each user login event. The
         supported modes are ``safe`` which will only store the user id or primary key, ``extended`` which will also store
         the username, email and full name and ``disabled``. Note that this feature requires ``DD_APPSEC_ENABLED`` to be
         set to ``true`` to work.
      
      version_added:
         v1.17.0: Added support to the Django integration. No other integrations support this configuration.

   DD_APPSEC_ENABLED:
     type: Boolean
     default: False
     description: Whether to enable AppSec monitoring.

   DD_APPSEC_OBFUSCATION_PARAMETER_KEY_REGEXP:
     default: |
       ``(?i)(?:p(?:ass)?w(?:or)?d|pass(?:_?phrase)?|secret|(?:api_?|private_?|public_?)key)|token|consumer_?(?:id|key|secret)|sign(?:ed|ature)|bearer|authorization``
     
     description: Sensitive parameter key regexp for obfuscation.

   DD_APPSEC_OBFUSCATION_PARAMETER_VALUE_REGEXP:
     default: |
         ``(?i)(?:p(?:ass)?w(?:or)?d|pass(?:_?phrase)?|secret|(?:api_?|private_?|public_?|access_?|secret_?)key(?:_?id)?|token|consumer_?(?:id|key|secret)|sign(?:ed|ature)?|auth(?:entication|orization)?)(?:\s*=[^;]|"\s*:\s*"[^"]+")|bearer\s+[a-z0-9\._\-]+|token:[a-z0-9]{13}|gh[opsu]_[0-9a-zA-Z]{36}|ey[I-L][\w=-]+\.ey[I-L][\w=-]+(?:\.[\w.+\/=-]+)?|[\-]{5}BEGIN[a-z\s]+PRIVATE\sKEY[\-]{5}[^\-]+[\-]{5}END[a-z\s]+PRIVATE\sKEY|ssh-rsa\s*[a-z0-9\/\.+]{100,}``
     
     description: Sensitive parameter value regexp for obfuscation.

   DD_APPSEC_RULES:
     type: String
     description: Path to a json file containing AppSec rules.

   DD_APPSEC_SCA_ENABLED:
     type: Boolean
     default: None
     description: Whether to enable/disable SCA (Software Composition Analysis).

   DD_APPSEC_MAX_STACK_TRACES:
     type: Integer
     default: 2
     description: Maximum number of stack traces reported for each trace.

   DD_APPSEC_MAX_STACK_TRACE_DEPTH:
     type: Integer
     default: 32
     description: Maximum number of frames in a stack trace report. 0 means no limit.

   DD_APPSEC_MAX_STACK_TRACE_DEPTH_TOP_PERCENT:
     type: Integer
     default: 75
     description: |
       Percentage of reported stack trace frames to be taken from the top of the stack in case of a stack trace truncation.
       For example, if DD_APPSEC_MAX_STACK_TRACE_DEPTH is set to 25 and DD_APPSEC_MAX_STACK_TRACE_DEPTH_TOP_PERCENT is set to 60,
       if a stack trace has more than 25 frames, the top 15 (25*0.6=15)frames and the bottom 10 frames will be reported.

   DD_APPSEC_STACK_TRACE_ENABLED:
     type: Boolean
     default: True
     description: Whether to enable stack traces in reports for ASM. Currently used for exploit prevention reports.

   DD_IAST_ENABLED:
     type: Boolean
     default: False
     description: Whether to enable IAST.

   DD_IAST_MAX_CONCURRENT_REQUESTS:
     type: Integer
     default: 2
     description: Number of requests analyzed at the same time.

   DD_IAST_DEDUPLICATION_ENABLED:
     type: Integer
     default: True
     description: Avoid sending vulnerabilities in the span if they have already been reported in the last hour.

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

   DD_IAST_STACK_TRACE_ENABLED:
     type: Boolean
     default: True
     description: Whether to enable stack traces in reports for Code Security/IAST.

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


Test Visibility
---------------

.. ddtrace-configuration-options::

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

   DD_CIVISIBILITY_LOG_LEVEL:
      type: String
      default: "info"
      
      description: |
         Configures the ``CIVisibility`` service to replace the default Datadog logger's stream handler with one that
         only displays messages related to the ``CIVisibility`` service, at a level of or higher than the given log
         level. The Datadog logger's file handler is unaffected. Valid, case-insensitive, values are ``critical``,
         ``error``, ``warning``, ``info``, or ``debug``. A value of ``none`` silently disables the logger. Note:
         enabling debug logging with the ``DD_TRACE_DEBUG`` environment variable overrides this behavior.
      
      version_added:
         v2.5.0:

   DD_TEST_SESSION_NAME:
     type: String
     default: (autodetected)
     
     description: |
        Configures the ``CIVisibility`` service to use the given string as the value of the ``test_session.name`` tag in
        test events. If not specified, this string will be constructed from the CI job id (if available) and the test
        command used to start the test session.
     
     version_added:
        v2.16.0:

   DD_CIVISIBILITY_RUM_FLUSH_WAIT_MILLIS:
     type: Integer
     default: 500

     description: |
        Configures how long, in milliseconds, the Selenium integration will wait after invoking the RUM flush function
        during calls to the driver's ``quit()`` or ``close()`` methods. This helps ensure that the call to the
        asynchronous function finishes before the driver is closed.

     version_added:
        v2.18.0:

Agent
-----

.. ddtrace-configuration-options::

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

   DD_DOGSTATSD_URL:
     type: URL
     
     default: |
         ``unix:///var/run/datadog/dsd.socket`` if available
         otherwise ``udp://localhost:8125``
     
     description: |
         The URL to use to connect the Datadog agent for Dogstatsd metrics. The url can start with
         ``udp://`` to connect using UDP or with ``unix://`` to use a Unix
         Domain Socket.

         Example for UDP url: ``DD_DOGSTATSD_URL=udp://localhost:8125``

         Example for UDS: ``DD_DOGSTATSD_URL=unix:///var/run/datadog/dsd.socket``

   DD_PATCH_MODULES:
     description: |
         Override the modules patched for this execution of the program. Must be
         a list in the ``module1:boolean,module2:boolean`` format. For example,
         ``boto:true,redis:false``.
     
     version_added:
       v0.55.0: |
           Formerly named ``DATADOG_PATCH_MODULES``

   DD_SITE:
     default: datadoghq.com
     
     description: |
         Specify which site to use for uploading profiles and logs. Set to
         ``datadoghq.eu`` to use EU site.

   DD_TAGS:
     description: |
         Set global tags to be attached to every span. Value must be either comma and/or space separated. e.g. ``key1:value1,key2:value2,key3``, ``key1:value key2:value2 key3`` or ``key1:value1, key2:value2, key3``.

         If a tag value is not supplied the value will be an empty string.
     
     version_added:
       v0.38.0: Comma separated support added
       v0.48.0: Space separated support added

   DD_TRACE_AGENT_TIMEOUT_SECONDS:
     type: Float
     default: 2.0
     description: The timeout in float to use to connect to the Datadog agent.

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

Logs
----

.. ddtrace-configuration-options::

   DD_LOGS_INJECTION:
     type: Boolean
     default: False
     description: Enables :ref:`Logs Injection`.

   DD_TRACE_DEBUG:
     type: Boolean
     default: False
     
     description: |
         Enables debug logging in the tracer.

         Can be used with `DD_TRACE_LOG_FILE` to route logs to a file.
     
     version_added:
       v0.41.0: |
           Formerly named ``DATADOG_TRACE_DEBUG``

   DD_TRACE_LOG_FILE:
     description: |
         Directs `ddtrace` logs to a specific file. Note: The default backup count is 1. For larger logs, use with ``DD_TRACE_LOG_FILE_SIZE_BYTES``.
         To fine tune the logging level, use with ``DD_TRACE_LOG_FILE_LEVEL``.

   DD_TRACE_LOG_FILE_LEVEL:
     default: DEBUG
     
     description: |
         Configures the ``RotatingFileHandler`` used by the `ddtrace` logger to write logs to a file based on the level specified.
         Defaults to `DEBUG`, but will accept the values found in the standard **logging** library, such as WARNING, ERROR, and INFO,
         if further customization is needed. Files are not written to unless ``DD_TRACE_LOG_FILE`` has been defined.

   DD_TRACE_LOG_FILE_SIZE_BYTES:
     type: Int
     default: 15728640
     
     description: |
         Max size for a file when used with `DD_TRACE_LOG_FILE`. When a log has exceeded this size, there will be one backup log file created.
         In total, the files will store ``2 * DD_TRACE_LOG_FILE_SIZE_BYTES`` worth of logs.

   DD_TRACE_STARTUP_LOGS:
     type: Boolean
     default: False
     description: Enable or disable start up diagnostic logging.

Sampling
--------

.. ddtrace-configuration-options::

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

   DD_TRACE_RATE_LIMIT:
     type: int
     default: 100
     
     description: |
        Maximum number of traces per second to sample. Set a rate limit to avoid the ingestion volume overages in the case of traffic spikes. This configuration
        is only applied when client based sampling is configured, otherwise agent based rate limits are used.
     
     version_added:
        v0.33.0:
        v2.15.0: Only applied when DD_TRACE_SAMPLE_RATE, DD_TRACE_SAMPLING_RULES, or DD_SPAN_SAMPLING_RULE are set.
        v3.0.0: Only applied when DD_TRACE_SAMPLING_RULES or DD_SPAN_SAMPLING_RULE are set.

   DD_TRACE_SAMPLING_RULES:
     type: JSON array
     
     description: |
         A JSON array of objects. Each object must have a “sample_rate”, and the “name”, “service”, "resource", and "tags" fields are optional. The “sample_rate” value must be between 0.0 and 1.0 (inclusive).

         **Example:** ``DD_TRACE_SAMPLING_RULES='[{"sample_rate":0.5,"service":"my-service","resource":"my-url","tags":{"my-tag":"example"}}]'``

         **Note** that the JSON object must be included in single quotes (') to avoid problems with escaping of the double quote (") character.'
     
     version_added:
       v1.19.0: added support for "resource"
       v1.20.0: added support for "tags"
       v2.8.0: added lazy sampling support, so that spans are evaluated at the end of the trace, guaranteeing more metadata to evaluate against.

Other
-----

.. ddtrace-configuration-options::

   DD_INSTRUMENTATION_TELEMETRY_ENABLED:
     type: Boolean
     default: True
     
     description: |
         Enables sending :ref:`telemetry <Instrumentation Telemetry>` events to the agent.

   DD_RUNTIME_METRICS_ENABLED:
     type: Boolean
     default: False
     
     description: |
         When used with ``ddtrace-run`` this configuration enables sending runtime metrics to Datadog.
         These metrics track the memory management and concurrency of the python runtime. 
         Refer to the following `docs <https://docs.datadoghq.com/tracing/metrics/runtime_metrics/python/>` _ for more information.

   DD_TRACE_EXPERIMENTAL_RUNTIME_ID_ENABLED:
     type: Boolean
     default: False
     version_added:
       v3.2.0: Adds initial support

     description: |
         Adds support for tagging runtime metrics with the current runtime ID. This is useful for tracking runtime metrics across multiple processes.
         Refer to the following `docs <https://docs.datadoghq.com/tracing/metrics/runtime_metrics/python/>` _ for more information.

   DD_TRACE_EXPERIMENTAL_FEATURES_ENABLED:
     type: string
     version_added:
       v3.2.0: Adds initial support and support for enabling experimental runtime metrics. 
     default: ""

     description: |
         Enables support for experimental ddtrace configurations. The supported configurations are: ``DD_RUNTIME_METRICS_ENABLED``.

   DD_SUBPROCESS_SENSITIVE_WILDCARDS:
     type: String
     
     description: |
         Add more possible matches to the internal list of subprocess execution argument scrubbing. Must be a comma-separated list and
         each item can take `fnmatch` style wildcards, for example: ``*ssn*,*personalid*,*idcard*,*creditcard*``.

   DD_USER_MODEL_EMAIL_FIELD:
      type: String
      default: ""
      
      description: |
         Field to be used to read the user email when using a custom ``User`` model for the automatic login events. This field will take precedence over automatic inference.
      
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

   DD_USER_MODEL_NAME_FIELD:
      type: String
      default: ""
      
      description: |
         Field to be used to read the user name when using a custom ``User`` model for the automatic login events. This field will take precedence over automatic inference.
      
      version_added:
         v1.15.0:

   DD_TRACE_BAGGAGE_TAG_KEYS:
      type: String
      default: "user.id,account.id,session.id"

      description: |
         A comma-separated list of baggage keys to be automatically attached as tags on spans.
         For each key specified, if a corresponding baggage key is present and has a non-empty value,
         the key-value pair will be added to the span's metadata using the key name formatted as ``baggage.<key>``.
         If you want to turn off this feature, set the configuration value to an empty string.
         When set to `*`, **all** baggage keys will be converted into span tags. Use with caution: this may unintentionally expose sensitive or internal data if not used intentionally.

      version_added: 
         v3.6.0:

.. _Unified Service Tagging: https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging/

.. _Configure the Datadog Tracing Library: https://docs.datadoghq.com/tracing/trace_collection/library_config/


Profiling
---------

.. ddtrace-envier-configuration:: ddtrace.settings.profiling:ProfilingConfig
   :recursive: true


Dynamic Instrumentation
-----------------------

.. ddtrace-envier-configuration:: ddtrace.settings.dynamic_instrumentation:DynamicInstrumentationConfig


Exception Replay
----------------

.. ddtrace-envier-configuration:: ddtrace.settings.exception_replay:ExceptionReplayConfig


Code Origin
-----------

.. ddtrace-envier-configuration:: ddtrace.settings.code_origin:CodeOriginConfig


Live Debugging
--------------

.. ddtrace-envier-configuration:: ddtrace.settings.live_debugging:LiveDebuggerConfig

Error Tracking
--------------
.. ddtrace-configuration-options::
  DD_ERROR_TRACKING_HANDLED_ERRORS:
      type: String
      default: ""

      description: |
          Report automatically handled errors to Error Tracking.
          Handled errors are also attached to spans through span events.

          Possible values are: ``user|third_party|all``. Report handled exceptions
          of user code, third party packages or both.

  DD_ERROR_TRACKING_HANDLED_ERRORS_INCLUDE:
      type: String
      default: ""

      description: |
          Comma-separated list of Python modules for which we report handled errors.
