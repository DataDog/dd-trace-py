# Changelog

Changelogs for versions not listed here can be found at https://github.com/DataDog/dd-trace-py/releases

---

## 2.11.6


### Bug Fixes

- library injection: Resolves an issue where the version of `attrs` installed by default on some Ubuntu installations was treated as incompatible with library injection
- Code Security: This fixes a bug in the IAST patching process where `AttributeError` exceptions were being caught, interfering with the proper application cycle.


---

## 2.10.7


### Bug Fixes

- CI Visibility: Resolves an issue where exceptions other than timeouts and connection errors raised while fetching the list of skippable tests for ITR were not being handled correctly and caused the tracer to crash.
- CI Visibility: Fixes a bug where `.git` was incorrectly being stripped from repository URLs when extracting service names, resulting in `g`, `i`, or `t` being removed (eg: `test-environment.git` incorrectly becoming `test-environmen`)
- openai: Fixes a bug where `asyncio.TimeoutError`s were not being propagated correctly from canceled OpenAI API requests.
- profiling: Fixes endpoing profiling for stack v2 when `DD_PROFILING_STACK_V2_ENABLED` is set.


---

## 2.9.6


### Bug Fixes

- CI Visibility: Resolves an issue where exceptions other than timeouts and connection errors raised while fetching the list of skippable tests for ITR were not being handled correctly and caused the tracer to crash.
- CI Visibility: Fixes a bug where `.git` was incorrectly being stripped from repository URLs when extracting service names, resulting in `g`, `i`, or `t` being removed (eg: `test-environment.git` incorrectly becoming `test-environmen`)
- SSI: Fixes incorrect file permissions on lib-injection images.
- Code Security: Adds null pointer checks when creating new objects ids.
- profiling: Fixes endpoing profiling for stack v2 when `DD_PROFILING_STACK_V2_ENABLED` is set.


---

## 2.11.4


### Bug Fixes

- CI Visibility: Resolves an issue where exceptions other than timeouts and connection errors raised while fetching the list of skippable tests for ITR were not being handled correctly and caused the tracer to crash.
- CI Visibility: Fixes a bug where `.git` was incorrectly being stripped from repository URLs when extracting service names, resulting in `g`, `i`, or `t` being removed (eg: `test-environment.git` incorrectly becoming `test-environmen`)
- LLM Observability: Resolves an issue where custom trace filters were being overwritten in forked processes.
- tracing: Fixes a side-effect issue with module import callbacks that could cause a runtime exception.
- LLM Observability: Resolves an issue where `session_id` was being defaulted to `trace_id` which was causing unexpected UI behavior.


---

## 2.12.0

### New Features

- openai: Introduces the `model` tag for openai integration metrics for consistency with the OpenAI SaaS Integration. It has the same value as `openai.request.model`.
- database_clients: Adds `server.address` tag to all `<database>.query` spans (ex: postgres.query). This tag stores the name of the database host.
- LLM Observability: Flushes the buffer of spans to be sent when the payload size would otherwise exceed the payload size limit for the event platform.
- LLM Observability: Span events that exceed the event platform event size limit (1 MB) will now have their inputs and outputs dropped.
- tracing: Adds `ddtrace.trace.Context` to the public api. This class can now be used to propagate context across execution boundaries (ex: threads).


### Deprecation Notes

- config: `DD_TRACE_128_BIT_TRACEID_LOGGING_ENABLED` is deprecated. Trace id logging format is now configured automatically.
- tracing: Deprecates all modules in the `ddtrace.contrib.[integration_name]` package. Use attributes exposed in `ddtrace.contrib.[integration_name].__all__` instead. The following are impacted:
  - `aioredis`, `algoliasearch`. `anthropic`, `aredis`, `asgi`, `asyncpg`, `aws_lambda`, `boto`, `botocore`, `bottle`, `cassandra`, `celery`, `cherrypy`, `consul`, `coverage`, `django`, `dogpile_cache`, `dramatiq`, `elasticsearch`, `falcon`, `fastapi`, `flask`, `flask_cache`, `futures`, `gevent`, `graphql`, `grpc`, `httplib`, `httpx`, `jinja2`, `kafka`, `kombu`, `langchain`, `logbook`, `logging`, `loguru`, `mako`, `mariadb`, `molten`, `mongoengine`, `mysql`, `mysqldb`, `openai`, `psycopg`, `pylibmc`, `pymemcache`, `pymongo`, `pymysql`, `pynamodb`, `pyodbc`, `pyramid`, `redis`, `rediscluster`, `requests`, `sanic`, `snowflake`, `sqlalchemy`, `sqlite3`, `starlette`, `structlog`, `subprocess`, `tornado`, `urllib`, `urllib3`, `vertica`, `webbrowser`, `wsgi`, `yaaredis`
### Bug Fixes

- CI Visibility: Resolves an issue where exceptions other than timeouts and connection errors raised while fetching the list of skippable tests for ITR were not being handled correctly and caused the tracer to crash.
- CI Visibility: Fixes a bug where `.git` was incorrectly being stripped from repository URLs when extracting service names, resulting in `g`, `i`, or `t` being removed (eg: `test-environment.git` incorrectly becoming `test-environmen`)
- LLM Observability: Resolves an issue where custom trace filters were being overwritten in forked processes.
- tracing: Fixes a side-effect issue with module import callbacks that could cause a runtime exception.
- LLM Observability: Resolves an issue where `session_id` was being defaulted to `trace_id`, which was causing unexpected UI behavior.
- LLM Observability: Resolves an issue where LLM Observability spans were not being submitted in forked processes, such as when using `celery` or `gunicorn` workers. The LLM Observability writer thread now automatically restarts when a forked process is detected.
- tracing: Fixes an issue with some module imports with native specs that don't support attribute assignments, resulting in a `TypeError` exception at runtime.
- tracing: Resolves an issue where `ddtrace` package files were published with incorrect file attributes.
- tracing: Resolves an issue where django db instrumentation could fail.
- openai: Fixes a bug where `asyncio.TimeoutError`s were not being propagated correctly from canceled OpenAI API requests.

- aiobotocore: Fixes an issue where the `_make_api_call` arguments were not captured correctly when using keyword arguments.
- tracing(django): Resolves a bug where ddtrace was exhausting a Django stream response before returning it to user.
- LLM Observability: Fixes an issue in the OpenAI integration where integration metrics would still be submitted even if `LLMObs.enable(agentless_enabled=True)` was set.
- internal: Fixes the `Already mutably borrowed` error when rate limiter is accessed across threads.
- internal: Fixes the `Already mutably borrowed` error by reverting back to pure-python rate limiter.
- Code Security: Adds null pointer checks when creating new objects ids.
- profiling: Fixes an issue where the profiler could erroneously try to load protobuf in autoinjected environments, where it is not available.
- crashtracking: Fixes an issue where crashtracking environment variables for Python were inconsistent with those used by other runtimes.
- profiling: Fixes endpoint profiling for stack v2 when `DD_PROFILING_STACK_V2_ENABLED` is set.
- profiling: Turns on the new native exporter when `DD_PROFILING_TIMELINE_ENABLED=True` is set.


---

## 2.11.3


### Bug Fixes

- ASM: Improves internal stability for the new fingerprinting feature.


---

## 2.11.2


### New Features

- openai: Introduces `model` tag for openai integration metrics for consistency with the OpenAI SaaS Integration. It has the same value as `openai.request.model`.

### Bug Fixes

- LLM Observability: Resolves an issue where LLM Observability spans were not being submitted in forked processes, such as when using `celery` or `gunicorn` workers. The LLM Observability writer thread now automatically restarts when a forked process is detected.
- openai: Fixes a bug where `asyncio.TimeoutError`s were not being propagated correctly from canceled OpenAI API requests.


---

## 2.11.1


### Bug Fixes

- tracing(django): This fix resolves a bug where ddtrace was exhausting a Django stream response before returning it to user.
- Fixed an issue with some module imports with native specs that don't support attribute assignments, resulting in a `TypeError` exception at runtime.
- internal: Fix `Already mutably borrowed` error by reverting back to pure-python rate limiter.
- This fix resolves an issue where `ddtrace` package files were published with incorrect file attributes.
- profiling: Fixes an issue where the profiler could erroneously try to load protobuf in autoinjected environments, where it is not available.
- Fixes an issue where crashtracking environment variables for Python were inconsistent with those used by other runtimes.
- profiling: Fixes endpoing profiling for stack v2, that is when `DD_PROFILING_STACK_V2_ENABLED` set.


---

## 2.11.0

### New Features

- ASM: This update introduces new Auto User Events support.

  ASM’s \[Account TakeOver (ATO) detection\](<https://docs.datadoghq.com/security/account_takeover_protection>) is now automatically monitoring \[all compatible user authentication frameworks\](<https://docs.datadoghq.com/security/application_security/enabling/compatibility/>) to detect attempted or leaked user credentials during an ATO campaign.

  To do so, the monitoring of the user activity is extended to now collect all forms of user IDs, including non-numerical forms such as usernames or emails. This is configurable with 3 different working modes: <span class="title-ref">identification</span> to send the user IDs in clear text; <span class="title-ref">anonymization</span> to send anonymized user IDs; or <span class="title-ref">disabled</span> to completely turn off any type of user ID collection (which leads to the disablement of the ATO detection).

  The default collection mode being used is <span class="title-ref">identification</span> and this is configurable in your remote service configuration settings in the \[service catalog\]( <https://app.datadog.com/security/appsec/inventory/services?tab=capabilities>) (clicking on a service), or with the service environment variable <span class="title-ref">DD_APPSEC_AUTO_USER_INSTRUMENTATION_MODE</span>.

  You can read more \[here\](<https://docs.datadoghq.com/security/account_takeover_protection>).

  New local configuration environment variables include:

  - \`DD_APPSEC_AUTOMATED_USER_EVENTS_TRACKING_ENABLED\`: Can be set to "true"/"1" (default if missing) or "false"/"0" (default if set to any other value). If set to false, the feature is completely disabled. If enabled, the feature is active.
  - \`DD_APPSEC_AUTO_USER_INSTRUMENTATION_MODE\`: Can be set to "identification" (default if missing), "anonymization", or "disabled" (default if the environment variable is set to any other value). *The values can be modified via remote configuration if the feature is active*. If set to "disabled", user events are not collected. Otherwise, user events are collected, using either plain text user_id (in identification mode) or hashed user_id (in anonymization mode).

  Additionally, an optional argument for the public API <span class="title-ref">track_user_login_success_event</span> and \`track_user_login_failure_event\`: <span class="title-ref">login_events_mode="auto"</span>. This allows manual instrumentation to follow remote configuration settings, enabling or disabling manual instrumentation with a single remote action on the Datadog UI.

  Also prevents non numerical user ids to be reported by default without user instrumentation in Django.

- Anthropic: Adds support for tracing message calls using tools.

- LLM Observability: Adds support for tracing Anthropic messages using tool calls.

- botocore: Adds support for overriding the default service name in botocore by either setting the environment variable `DD_BOTOCORE_SERVICE` or configuring it via <span class="title-ref">ddtrace.config.botocore\["service"\]</span>.

- azure: Removes the restrictions on the tracer to only run the mini-agent on the consumption plan. The mini-agent now runs regardless of the hosting plan

- ASM: Adds Threat Monitoring support for gRPC.

- Code Security: add propagation for GRPC server sources.

- LLM Observability: This introduces improved support for capturing tool call responses from the OpenAI and Anthropic integrations.

- LLM Observability: This introduces the agentless mode configuration for LLM Observability. To enable agentless mode, set the environment variable `DD_LLMOBS_AGENTLESS_ENABLED=1`, or use the enable option `LLMObs.enable(agentless_enabled=True)`.

- LLM Observability: Function decorators now support tracing asynchronous functions.

- LLM Observability: This introduces automatic input/output annotation for task/tool/workflow/agent/retrieval spans traced by function decorators. Note that manual annotations for input/output values will override automatic annotations.

- LLM Observability: The OpenAI integration now submits embedding spans to LLM Observability.

- LLM Observability: All OpenAI model parameters specified in a completion/chat completion request are now captured.

- LLM Observability: This changes OpenAI-generated LLM Observability span names from `openai.request` to `openai.createCompletion`, `openai.createChatCompletion`, and `openai.createEmbedding` for completions, chat completions, and embeddings spans, respectively.

- LLM Observability: This introduces the agent proxy mode for LLM Observability. By default, LLM Observability spans will be sent to the Datadog agent and then forwarded to LLM Observability. To continue submitting data directly to LLM Observability without the Datadog agent, set `DD_LLMOBS_AGENTLESS_ENABLED=1` or set programmatically using `LLMObs.enable(agentless_enabled=True)`.

- LLM Observability: The Langchain integration now submits embedding spans to LLM Observability.

- LLM Observability: The `LLMObs.annotate()` method now replaces non-JSON serializable values with a placeholder string `[Unserializable object: <string representation of object>]` instead of rejecting the annotation entirely.

- pylibmc: adds traces for memcached add command

- ASM: This introduces fingerprinting with libddwaf 1.19.1

- Database Monitoring: Adds Database Monitoring (DBM) trace propagation for postgres databases used through Django.

- langchain: Tags tool calls on chat completions.

- LLM Observability: Adds retry logic to the agentless span writer to mitigate potential networking issues, like timeouts or dropped connections.

- ASM: This introduces Command Injection support for Exploit Prevention on os.system only.

- ASM: This introduces suspicious attacker blocking with libddwaf 1.19.1
### Upgrade Notes

- ASM: This upgrade prevents the WAF from being invoked for exploit prevention if the corresponding rules are not enabled via remote configuration.
### Deprecation Notes

- ASM: The environment variable DD_APPSEC_AUTOMATED_USER_EVENTS_TRACKING is deprecated and will be removed in the next major release. Instead of DD_APPSEC_AUTOMATED_USER_EVENTS_TRACKING, you should use DD_APPSEC_AUTO_USER_INSTRUMENTATION_MODE. The "safe" and "extended" modes are deprecated and have been replaced by "anonymization" and "identification", respectively.
- botocore: All methods in botocore/patch.py except `patch()` and `unpatch()` are deprecated and will be removed in version 3.0.0.
- consul: All methods in consul/patch.py except `patch()` and `unpatch()` are deprecated and will be removed in version 3.0.0.
- psycopg: All methods in psycopg/patch.py except `patch()` and `unpatch()` are deprecated and will be removed in version 3.0.0.
- pylibmc: All methods in pylibmc/patch.py except `patch()` and `unpatch()` are deprecated and will be removed in version 3.0.0.
- pymemcache: All methods in pymemcache/patch.py except `patch()` and `unpatch()` are deprecated and will be removed in version 3.0.0.
- pymongo: All methods in pymongo/patch.py except `patch()` and `unpatch()` are deprecated and will be removed in version 3.0.0.
- pymysql: All methods in pymysql/patch.py except `patch()` and `unpatch()` are deprecated and will be removed in version 3.0.0.
- pynamodb: All methods in pynamodb/patch.py except `patch()` and `unpatch()` are deprecated and will be removed in version 3.0.0.
- pyodbc: All methods in pyodbc/patch.py except `patch()` and `unpatch()` are deprecated and will be removed in version 3.0.0.
- pyramid: All methods in pyramid/patch.py except `patch()` and `unpatch()` are deprecated and will be removed in version 3.0.0.
- exception replay: The `DD_EXCEPTION_DEBUGGING_ENABLED` environment variable has been deprecated in favor of `DD_EXCEPTION_REPLAY_ENABLED`. The old environment variable will be removed in a future major release.
- ASM: This removes the partial auto instrumentation of flask login. It was giving only partial and possibly confusing picture of the login activity. We recommend customers to switch to \[manual instrumentation\](<https://docs.datadoghq.com/security/application_security/threats/add-user-info/?tab=loginsuccess&code-lang=python#adding-business-logic-information-login-success-login-failure-any-business-logic-to-traces>).
### Bug Fixes

- LLM Observability: Fixes an issue in the OpenAI integration where integration metrics would still be submitted even if `LLMObs.enable(agentless_enabled=True)` was set.
- Code Security: add null pointer checks when creating new objects ids.

- Code Security: add encodings.idna to the IAST patching denylist to avoid problems with gevent.
- Code Security: add the boto package to the IAST patching denylist.
- Code Security: fix two small memory leaks with Python 3.11 and 3.12.
- CI Visibility: Fixes an issue where the pytest plugin would crash if the git binary was absent
- CI Visibility: fixes incorrect URL for telemetry intake in EU that was causing missing telemetry data and SSL error log messages.
- celery: changes `error.message` span tag to no longer include the traceback that is already included in the `error.stack` span tag.
- CI Visibility: fixes source file information that would be incorrect in certain decorated / wrapped scenarios and forces paths to be relative to the repository root, if present.
- futures: Fixes inconsistent behavior with `concurrent.futures.ThreadPoolExecutor` context propagation by passing the current trace context instead of the currently active span to tasks. This prevents edge cases of disconnected spans when the task executes after the parent span has finished.
- kafka: Fixes `ArgumentError` raised when injecting span context into non-existent Kafka message headers.
- botocore: Fixes Botocore Kinesis span parenting to use active trace context if a propagated child context is not found instead of empty context.
- langchain: This fix resolves an issue where the wrong langchain class name was being used to check for Pinecone vectorstore instances.
- LLM Observability: This resolves a typing hint error in the `ddtrace.llmobs.utils.Documents` helper class constructor where type hints did not accept input dictionaries with integer or float values.
- LLM Observability: This fix resolves an issue where the OpenAI, Anthropic, and AWS Bedrock integrations were always setting `temperature` and `max_tokens` parameters to LLM invocations. The OpenAI integration in particular was setting the wrong `temperature` default values. These parameters are now only set if provided in the request.
- opentelemetry: Resolves circular imports raised by the OpenTelemetry API when the `ddcontextvars_context` entrypoint is loaded. This resolves an incompatibility introduced in `opentelemetry-api==1.25.0`.
- opentelemetry: Resolves an issue where the `get_tracer` function would raise a `TypeError` when called with the `attribute` argument. This resolves an incompatibility introduced in `opentelemetry-api==1.26.0`.
- psycopg: Ensures traced async cursors return an asynchronous iterator object.
- redis: This fix resolves an issue in the redis exception handling where an UnboundLocalError was raised instead of the expected BaseException.
- ASM: This fix resolves an issue where the <span class="title-ref">requests</span> integration would not propagate when apm is opted out (i.e. in ASM Standalone).
- profiling: Fixes an issue where task information coming from echion was encoded improperly, which could segfault the application.
- tracing: fixes a potential crash where using partial flushes and `tracer.configure()` could result in an IndexError
- tracer: This fix resolves an issue where the tracer was not starting properly on a read-only file system.
- internal: fixes an issue where some pathlib functions return OSError on Windows.
- ASM: This fix resolves an issue where the WAF could be disabled if the ASM_DD rule file was not found in Remote Config.
- flask: Fix scenarios when using flask-like frameworks would cause a crash because of patching issues on startup.
- Code Security: Logs warning instead of throwing an exception in the native module if IAST is not enabled by env var.
- Code Security: fix potential infinite loop with path traversal when the analyze quota has been exceeded.
- wsgi: Ensures the status of wsgi Spans are not set to error when a `StopIteration` exception is raised marked the span as an error. With this change, `StopIteration` exceptions in this context will be ignored.
- langchain: tag non-dict inputs to LCEL chains appropriately. Non-dict inputs are stringified, and dict inputs are tagged by key-value pairs.
- tracing: Updates `DD_HEADER_TAGS` and `DD_TAGS` to support the following formats: `key1,key2,key3`, `key1:val,key2:val,key3:val3`, `key1:val key2:val key3:val3`, and `key1 key2 key3`. Key value pairs that do not match an expected format will be logged and ignored by the tracer.
- loguru: This fix avoids copying attributes from a log record's "extras" field to the record's top level if those attributes were not added by the Datadog integration.
- opentelemetry: Resolves an edge case where distributed tracing headers could be generated before a sampling decision is made, resulting in dropped spans in downstream services.
- profiling: captures lock usages with `with` context managers, e.g. `with lock:`
- profiling: propagates `runtime_id` tag to libdatadog exporter. It is a unique string identifier for the profiled process. For example, Thread Timeline visualization uses it to distinguish different processes.
- profiling: show lock init location in Lock Name and hide profiler internal frames from Stack Frame in Timeline Details tab.
- ASM: This fix resolves an issue where ASM one click feature could fail to deactivate ASM.
- redis: This fix resolves an issue in redis utils where a variable may not be declared within a try/catch
### Other Changes

- LLM Observability: the SDK allowed users to submit an unsupported <span class="title-ref">numerical</span> evaluation metric type. All evaluation metric types submitted with <span class="title-ref">numerical</span> type will now be automatically converted to a <span class="title-ref">score</span> type. As an alternative to using the <span class="title-ref">numerical type, use \`score</span> instead.
- LLM Observability: `LLMObs.submit_evaluation()` requires a Datadog API key to send custom evaluations to LLM Observability. If an API key is not set using either `DD_API_KEY` or `LLMObs.enable(api_key="<api-key>")`, this method will log a warning and return `None`.


---

## 2.10.6


### Bug Fixes

- tracing(django): Resolves a bug where `ddtrace` was exhausting a Django stream response before returning it to user.
- internal: Fixes `Already mutably borrowed` error by reverting back to pure-python rate limiter.


---

## 2.8.7


### Bug Fixes

- opentelemetry: Resolves circular imports raised by the OpenTelemetry API when the `ddcontextvars_context` entrypoint is loaded. This resolves an incompatibility introduced in `opentelemetry-api==1.25.0`.
- opentelemetry: Resolves an issue where the `get_tracer` function would raise a `TypeError` when called with the `attribute` argument. This resolves an incompatibility introduced in `opentelemetry-api==1.26.0`.
- opentelemetry: Resolves an edge case where distributed tracing headers could be generated before a sampling decision is made, resulting in dropped spans in downstream services.


---

## 2.10.4


### Bug Fixes

- SSI: Fixes incorrect file permissions on lib-injection images.
- profiling: Shows lock init location in Lock Name and hides profiler internal frames from Stack Frame in Timeline Details tab.


---

## 2.10.3

### Bug Fixes

- ASM: This fix resolves an issue where the WAF could be disabled if the ASM_DD rule file was not found in Remote Config.
- CI Visibility: Fixes an issue where the pytest plugin would crash if the git binary was absent
- CI Visibility: Fixes incorrect URL for telemetry intake in EU that was causing missing telemetry data and SSL error log messages.
- Code Security: Add encodings.idna to the IAST patching denylist to avoid problems with gevent.
- internal: Fixes an issue where some pathlib functions return OSError on Windows.
- opentelemetry: Resolves an edge case where distributed tracing headers could be generated before a sampling decision is made, resulting in dropped spans in downstream services.

---

## 2.9.5

### Bug Fixes

- ASM: This fix resolves an issue where the WAF could be disabled if the ASM_DD rule file was not found in Remote Config.
- CI Visibility: Fixes an issue where the pytest plugin would crash if the git binary was absent
- CI Visibility: Fixes incorrect URL for telemetry intake in EU that was causing missing telemetry data and SSL error log messages.
- Code Security: fix potential infinite loop with path traversal when the analyze quota has been exceeded.
- opentelemetry: Resolves an edge case where distributed tracing headers could be generated before a sampling decision is made, resulting in dropped spans in downstream services.
- profiling: captures lock usages with `with` context managers, e.g. `with lock:`
- profiling: propagates `runtime_id` tag to libdatadog exporter. It is a unique string identifier for the profiled process. For example, Thread Timeline visualization uses it to distinguish different processes.
- psycopg: Ensures traced async cursors return an asynchronous iterator object.

---

## 2.8.6

### Bug Fixes

- ASM: This fix resolves an issue where an org could not customize actions through remote config.
- Code Security: add the boto package to the IAST patching denylist.
- CI Visibility: Fixes an issue where the pytest plugin would crash if the git binary was absent
- CI Visibility: fixes source file information that would be incorrect in certain decorated / wrapped scenarios and forces paths to be relative to the repository root, if present.
- CI Visibility: fixes that traces were not properly being sent in agentless mode, and were otherwise not properly attached to the test that started them
- openai: This fix resolves an issue where specifying `None` for streamed chat completions resulted in a `TypeError`.
- openai: This fix removes patching for the edits and fine tunes endpoints, which have been removed from the OpenAI API.
- openai: This fix resolves an issue where streamed OpenAI responses raised errors when being used as context managers.
- profiling: Fixes an issue where task information coming from echion was encoded improperly, which could segfault the application.
- tracing: fixes a potential crash where using partial flushes and `tracer.configure()` could result in an IndexError
- tracing: Fixes an issue where `DD_TRACE_SPAN_TRACEBACK_MAX_SIZE` was not applied to exception tracebacks.
- tracing: This fix resolves an issue where importing `asyncio` after a trace has already been started will reset the currently active span.
- flask: Fix scenarios when using flask-like frameworks would cause a crash because of patching issues on startup.
- profiling: captures lock usages with `with` context managers, e.g. `with lock:`
- profiling: propagates `runtime_id` tag to libdatadog exporter. It is a unique string identifier for the profiled process. For example, Thread Timeline visualization uses it to distinguish different processes.

---

## 2.10.2

### Bug Fixes

- lib-injection: This fix resolves an issue with docker layer caching and the final lib-injection image size.
- psycopg: Ensures traced async cursors return an asynchronous iterator object.
- tracer: This fix resolves an issue where the tracer was not starting properly on a read-only file system.
- Code Security: fix potential infinite loop with path traversal when the analyze quota has been exceeded.
- profiling: captures lock usages with `with` context managers, e.g. `with lock:`
- profiling: propagates `runtime_id` tag to libdatadog exporter. It is a unique string identifier for the profiled process. For example, Thread Timeline visualization uses it to distinguish different processes.

---

## 2.10.1


### Bug Fixes

- langchain: This fix resolves an issue where the wrong langchain class name was being used to check for Pinecone vectorstore instances.
- opentelemetry: Resolves circular imports raised by the OpenTelemetry API when the `ddcontextvars_context` entrypoint is loaded. This resolves an incompatibility introduced in `opentelemetry-api==1.25.0`.
- opentelemetry: Resolves an issue where the `get_tracer` function would raise a `TypeError` when called with the `attribute` argument. This resolves an incompatibility introduced in `opentelemetry-api==1.26.0`.
- ASM: This fix resolves an issue where ASM one click feature could fail to deactivate ASM.


---


## 2.10.0

### New Features

- botocore: Adds support for overriding the default service name in botocore by either setting the environment variable `DD_BOTOCORE_SERVICE` or configuring it via `ddtrace.config.botocore["service"]`.
- Database Monitoring: Adds Database Monitoring (DBM) trace propagation for postgres databases used through Django.
- Anthropic: Adds support for tracing message calls using tools.
- LLM Observability: Adds support for tracing Anthropic messages using tool calls.
- azure: Removes the restrictions on the tracer to only run the mini-agent on the consumption plan. The mini-agent now runs regardless of the hosting plan
- Anthropic: Adds support for tracing synchronous and asynchronous message streaming.
- LLM Observability: Adds support for tracing synchronous and asynchronous message streaming.
- SSI: Introduces generic safeguards for automatic instrumentation when using single step install in the form of early exit conditions. Early exit from instrumentation is triggered if a version of software in the environment is not explicitly supported by ddtrace. The Python runtime itself and many Python packages are checked for explicit support on the basis of their version.
- langchain: Introduces support for `langchain==0.2.0` by conditionally patching the `langchain-community` module if available, which is an optional dependency for `langchain>=0.2.0`. See the langchain integration docs for more details.
- LLM Observability: Adds support to automatically submit Anthropic chat messages to LLM Observability.

- tracer: This introduces the tracer flare functionality. Currently the tracer flare includes the tracer logs and tracer configurations.

- Code Security: Expands SSRF vulnerability support for Code Security and Exploit Prevention for the modules `urllib3`, `http.client`, `webbrowser` and `urllib.request`.

- ASM: This introduces full support for exploit prevention in the python tracer.  
  - LFI (via standard API open)
  - SSRF (via standard API urllib or third party requests)

  with monitoring and blocking feature, telemetry and span metrics reports.

- ASM: This introduces SQL injection support for exploit prevention.

- anthropic: This introduces tracing support for anthropic chat messages.  
  See [the docs](https://ddtrace.readthedocs.io/en/stable/integrations.html#anthropic) for more information.

- ASM: This introduces "Standalone ASM", a feature that disables APM in the tracer but keeps ASM enabled. In order to enable it, set the environment variables `DD_APPSEC_ENABLED=1` and `DD_EXPERIMENTAL_APPSEC_STANDALONE_ENABLED=1`.

- LLM Observability: This introduces the LLM Observability SDK, which enhances the observability of Python-based LLM applications. See the [LLM Observability Overview](https://docs.datadoghq.com/tracing/llm_observability/) or the [SDK documentation](https://docs.datadoghq.com/tracing/llm_observability/sdk) for more information about this feature.

- opentelemetry: Adds support for span events.

- tracing: Ensures the following OpenTelemetry environment variables are mapped to an equivalent Datadog configuration (datadog environment variables taking precedence in cases where both are configured):

      OTEL_SERVICE_NAME -> DD_SERVICE
      OTEL_LOG_LEVEL -> DD_TRACE_DEBUG
      OTEL_PROPAGATORS -> DD_TRACE_PROPAGATION_STYLE
      OTEL_TRACES_SAMPLER -> DD_TRACE_SAMPLE_RATE
      OTEL_TRACES_EXPORTER -> DD_TRACE_ENABLED
      OTEL_METRICS_EXPORTER -> DD_RUNTIME_METRICS_ENABLED
      OTEL_LOGS_EXPORTER -> none
      OTEL_RESOURCE_ATTRIBUTES -> DD_TAGS
      OTEL_SDK_DISABLED -> DD_TRACE_OTEL_ENABLED

- otel: Adds support for generating Datadog trace metrics using OpenTelemetry instrumentations

### Known Issues

- Code Security: Security tracing for the `builtins.open` function is experimental and may not be stable. This aspect is not replaced by default.
- grpc: Tracing for the `grpc.aio` clients and servers is experimental and may not be stable. This integration is now disabled by default.

### Deprecation Notes

- Removes the deprecated sqlparse dependency.
- LLM Observability: `DD_LLMOBS_APP_NAME` is deprecated and will be removed in the next major version of ddtrace. As an alternative to `DD_LLMOBS_APP_NAME`, you can use `DD_LLMOBS_ML_APP` instead. See the [SDK setup documentation](https://docs.datadoghq.com/tracing/llm_observability/sdk/#setup) for more details on how to configure the LLM Observability SDK.

### Bug Fixes

- Code Security: Logs warning instead of throwing an exception in the native module if IAST is not enabled by env var.
- redis: This fix resolves an issue in redis utils where a variable may not be declared within a try/catch

- Code Security: Adds the `boto` package to the IAST patching denylist.
- celery: Changes `error.message` span tag to no longer include the traceback that is already included in the `error.stack` span tag.
- CI Visibility: Fixes source file information that would be incorrect in certain decorated / wrapped scenarios and forces paths to be relative to the repository root, if present.
- LLM Observability: This resolves a typing hint error in the `ddtrace.llmobs.utils.Documents` helper class constructor where type hints did not accept input dictionaries with integer or float values.
- LLM Observability: This fix resolves an issue where the OpenAI, Anthropic, and AWS Bedrock integrations were always setting `temperature` and `max_tokens` parameters to LLM invocations. The OpenAI integration in particular was setting the wrong `temperature` default values. These parameters are now only set if provided in the request.
- redis: This fix resolves an issue in the redis exception handling where an UnboundLocalError was raised instead of the expected BaseException.
- ASM: This fix resolves an issue where the requests integration would not propagate when apm is opted out (i.e. in ASM Standalone).
- profiling: Fixes an issue where task information coming from echion was encoded improperly, which could segfault the application.
- tracing: Fixes a potential crash where using partial flushes and `tracer.configure()` could result in an `IndexError`.
- flask: Fixes scenarios when using flask-like frameworks would cause a crash because of patching issues on startup.
- wsgi: Ensures the status of wsgi Spans are not set to error when a `StopIteration` exception is raised marked the span as an error. With this change, `StopIteration` exceptions in this context will be ignored.
- langchain: Tags non-dict inputs to LCEL chains appropriately. Non-dict inputs are stringified, and dict inputs are tagged by key-value pairs.
- langchain: Fixes an issue of langchain patching errors due to the `langchain-community` module becoming an optional dependency in `langchain>=0.2.0`. The langchain integration now conditionally patches `langchain-community` methods if it is available. See the langchain integration docs for more details.

- ASM: This fix resolves an issue where an org could not customize actions through remote config.
- ASM: Protects against potentially returning `None` when tainting a gRPC message.
- botocore: This fix adds additional key name checking and appropriate defaults for responses from Cohere and Amazon models.
- Tracer: This fix resolves an issue where importing `asyncio` after a trace has already been started will reset the currently active span.
- CI Visibility: Fixes traces that were not properly being sent in agentless mode, and were otherwise not properly attached to the test that started them
- grpc: Fixes a bug in the `grpc.aio` support specific to streaming responses.
- openai: This fix resolves an issue where specifying `n=None` for streamed chat completions resulted in a `TypeError`.
- openai: This fix removes patching for the edits and fine tunes endpoints, which have been removed from the OpenAI API.
- openai: This fix resolves an issue where streamed OpenAI responses raised errors when being used as context managers.
- tracing: Ensures span links generated by distributed tracing headers record the correct sampling decision.
- telemetry: This fix resolves an issue when using `pytest` + `gevent` where the telemetry writer was eager initialized by `pytest` entrypoints loading of our plugin causing a potential dead lock.
- tracing: Fixes an issue where `DD_TRACE_SPAN_TRACEBACK_MAX_SIZE` was not applied to exception tracebacks.
- Code Security: This fixes a bug in the AST patching process where `ImportError` exceptions were being caught, interfering with the proper application cycle if an `ImportError` was expected."
- Code Security: Ensure IAST propagation does not raise side effects related to Magic methods.
- Code Security: Fixes a potential memory corruption when the context was reset.
- langchain: This fix resolves an issue where specifying inputs as a keyword argument for batching on chains caused a crash.
- Code Security: Avoids calling terminate on the extend and join aspect when an exception is raised.
- tracing: Ensures spans are rate limited at the expected rate (100 spans per second by default). Previously long running spans would set the rate limiter to set an invalid window and this could cause the next trace to be dropped.
- RemoteConfig: This fix resolves an issue where remote config did not work for the tracer when using an agent that would add a flare item to the remote config payload. With this fix, the tracer will now correctly pull out the lib_config we need from the payload in order to implement remote config changes properly.
- opentelemetry: Records exceptions on spans in a manner that is consistent with the [otel specification](https://opentelemetry.io/docs/specs/otel/trace/exceptions/#recording-an-exception)
- tracing: Ensures W3C tracecontext headers take precedence over all other header formats when incoming headers reference different spans in the same trace.

### Other Changes

- LLM Observability: The SDK allowed users to submit an unsupported `numerical` evaluation metric type. All evaluation metric types submitted with `numerical` type will now be automatically converted to a `score` type. As an alternative to using the `numerical` type, use `score` instead.

- lib-injection: Updates base Alpine image to 3.20.

---

## 2.9.4


### Bug Fixes

- langchain: This fix resolves an issue where the wrong langchain class name was being used to check for Pinecone vectorstore instances.
- opentelemetry: Resolves circular imports raised by the OpenTelemetry API when the `ddcontextvars_context` entrypoint is loaded. This resolves an incompatibility introduced in `opentelemetry-api==1.25.0`.
- opentelemetry: Resolves an issue where the `get_tracer` function would raise a `TypeError` when called with the `attribute` argument. This resolves an incompatibility introduced in `opentelemetry-api==1.26.0`.
- redis: Resolves an issue in the `redis` exception handling where an `UnboundLocalError` was raised instead of the expected `BaseException`.
- Code Security: Logs warning instead of throwing an exception in the native module if IAST is not enabled by env var.
- langchain: Fixes an issue of `langchain` patching errors due to the `langchain-community` module becoming an optional dependency in `langchain>=0.2.0`. The `langchain` integration now conditionally patches `langchain-community` methods if it is available. See the langchain integration docs for more details.
- langchain: Resolves incompatibilities with langchain==0.2.0
- ASM: Resolves an issue where ASM one click feature could fail to deactivate ASM.


---

## 2.9.3


### Bug Fixes

- Code Security: Adds `encodings.idna` to the IAST patching denylist to avoid problems with gevent.
- Code Security: Adds the boto package to the IAST patching denylist.
- celery: Changes `error.message` span tag to no longer include the traceback that is already included in the `error.stack` span tag.
- CI Visibility: Fixes source file information that would be incorrect in certain decorated / wrapped scenarios, and forces paths to be relative to the repository root, if present.
- LLM Observability: Resolves a typing hint error in the `ddtrace.llmobs.utils.Documents` helper class constructor where type hints did not accept input dictionaries with integer or float values.
- LLM Observability: Resolves an issue where the OpenAI and AWS Bedrock integrations were always setting `temperature` and `max_tokens` parameters to LLM invocations. The OpenAI integration in particular was setting the wrong `temperature` default values. These parameters are now only set if provided in the request.
- profiling: Fixes an issue where task information coming from `echion` was encoded improperly, which could segfault the application.
- tracing: Fixes a potential crash where using partial flushes and `tracer.configure()` could result in an `IndexError`.
- internal: Fixes an issue where some `pathlib` functions return `OSError`g on Windows.
- flask: Fixes scenarios when using flask-like frameworks would cause a crash because of patching issues on startup.
- wsgi: Ensures the status of wsgi Spans are not set to error when a `StopIteration` exception is raised marked the span as an error. With this change, `StopIteration` exceptions in this context will be ignored.
- langchain: Tags non-dict inputs to LCEL chains appropriately. Non-dict inputs are stringified, and dict inputs are tagged by key-value pairs.

### Other Changes

- LLM Observability: The SDK allowed users to submit an unsupported `numerical` evaluation metric type. All evaluation metric types submitted with `numerical` type will now be automatically converted to a `score` type. As an alternative to using the `numerical` type, use `score` instead.


---

## 2.9.2


### Bug Fixes

- futures: Fixes inconsistent behavior with `concurrent.futures.ThreadPoolExecutor` context propagation by passing the current trace context instead of the currently active span to tasks. This prevents edge cases of disconnected spans when the task executes after the parent span has finished.

### Other Changes

- lib-injection: Updates base Alpine image to 3.20.


---

## 2.9.1


### Deprecation Notes

- Removes the deprecated sqlparse dependency.


---

## 2.9.0

### New Features

- LLM Observability: This introduces the LLM Observability SDK, which enhances the observability of Python-based LLM applications. See the [LLM Observability Overview](https://docs.datadoghq.com/tracing/llm_observability/) or the [SDK documentation](https://docs.datadoghq.com/tracing/llm_observability/sdk) for more information about this feature.
- ASM:  Application Security Management (ASM) introduces its new "Exploit Prevention" feature in public beta, a new type of in-app security monitoring that detects and blocks vulnerability exploits. This introduces full support for exploit prevention in the python tracer.  
  - LFI (via standard API open)
  - SSRF (via standard API urllib or third party requests)

  with monitoring and blocking features, telemetry, and span metrics reports.

- opentelemetry: Adds support for span events.

- tracing: Ensures the following OpenTelemetry environment variables are mapped to an equivalent Datadog configuration (datadog environment variables taking precedence in cases where both are configured):

      OTEL_SERVICE_NAME -> DD_SERVICE
      OTEL_LOG_LEVEL -> DD_TRACE_DEBUG
      OTEL_PROPAGATORS -> DD_TRACE_PROPAGATION_STYLE
      OTEL_TRACES_SAMPLER -> DD_TRACE_SAMPLE_RATE
      OTEL_TRACES_EXPORTER -> DD_TRACE_ENABLED
      OTEL_METRICS_EXPORTER -> DD_RUNTIME_METRICS_ENABLED
      OTEL_RESOURCE_ATTRIBUTES -> DD_TAGS
      OTEL_SDK_DISABLED -> DD_TRACE_OTEL_ENABLED

- otel: Adds support for generating Datadog trace metrics using OpenTelemetry instrumentations
- aiomysql, asyncpg, mysql, mysqldb, pymysql: Adds Database Monitoring (DBM) for remaining mysql and postgres integrations lacking support.
- (aiomysql, aiopg): Implements span service naming determination to be consistent with other database integrations.
- ASM: This introduces the capability to enable or disable SCA using the environment variable DD_APPSEC_SCA_ENABLED. By default this env var is unset and in that case it doesn't affect the product.
- Code Security: Taints strings from gRPC messages.
- botocore: This introduces tracing support for bedrock-runtime embedding operations.
- Vulnerability Management for Code-level (IAST): Enables IAST in the application. Needed to start application with `ddtrace-run [your-application-run-command]` prior to this release. Now, you can also activate IAST with the `patch_all` function.
- langchain: This adds tracing support for LCEL (LangChain Expression Language) chaining syntax. This change specifically adds synchronous and asynchronous tracing support for the `invoke` and `batch` methods.

### Known Issues

- Code Security: Security tracing for the `builtins.open` function is experimental and may not be stable. This aspect is not replaced by default.
- grpc: Tracing for the `grpc.aio` clients and servers is experimental and may not be stable. This integration is now disabled by default.

### Upgrade Notes

- aiopg: Upgrades supported versions to \>=1.2. Drops support for 0.x versions.

### Deprecation Notes

- LLM Observability: `DD_LLMOBS_APP_NAME` is deprecated and will be removed in the next major version of ddtrace. As an alternative to `DD_LLMOBS_APP_NAME`, you can use `DD_LLMOBS_ML_APP` instead. See the [SDK setup documentation](https://docs.datadoghq.com/tracing/llm_observability/sdk/#setup) for more details on how to configure the LLM Observability SDK.

### Bug Fixes

- opentelemetry: Records exceptions on spans in a manner that is consistent with the [otel specification](https://opentelemetry.io/docs/specs/otel/trace/exceptions/#recording-an-exception)
- ASM: Resolves an issue where an org could not customize actions through remote config.
- Resolves an issue where importing `asyncio` after a trace has already been started will reset the currently active span.
- grpc: Fixes a bug in the `grpc.aio` integration specific to streaming responses.
- openai: Resolves an issue where specifying `n=None` for streamed chat completions resulted in a `TypeError`.
- openai: Removes patching for the edits and fine tunes endpoints, which have been removed from the OpenAI API.
- openai: Resolves an issue where streamed OpenAI responses raised errors when being used as context managers.
- tracing: Fixes an issue where `DD_TRACE_SPAN_TRACEBACK_MAX_SIZE` was not applied to exception tracebacks.
- Code Security: Ensures IAST propagation does not raise side effects related to Magic methods.
- Code Security: Fixes a potential memory corruption when the context was reset.
- langchain: Resolves an issue where specifying inputs as a keyword argument for batching on chains caused a crash.
- Code Security: Avoids calling `terminate` on the `extend` and `join` aspect when an exception is raised.
- botocore: Adds additional key name checking and appropriate defaults for responses from Cohere and Amazon models.
- telemetry: Resolves an issue when using `pytest` + `gevent` where the telemetry writer was eager initialized by `pytest` entry points loading of our plugin causing a potential dead lock.
- Code Security: Fixes a bug in the AST patching process where `ImportError` exceptions were being caught, interfering with the proper application cycle if an `ImportError` was expected."
- RemoteConfig: Resolves an issue where remote config did not work for the tracer when using an agent that would add a flare item to the remote config payload. With this fix, the tracer will now correctly pull out the lib_config we need from the payload in order to implement remote config changes properly.
- Code Security: Fixes setting the wrong source on map elements tainted from `taint_structure`.
- Code Security: Fixes an issue where the AST patching process fails when the origin of a module is reported as None, raising a `FileNotFoundError`.
- CI Visibility: Fixes an issue where tests were less likely to be skipped due to ITR skippable tests requests timing out earlier than they should
- Code Security: Solves an issue with fstrings where formatting was not applied to int parameters
- tracing: Resolves an issue where sampling rules were not matching correctly on float values that had a 0 decimal value. Sampling rules now evaluate such values as integers.
- langchain: Resolves an issue where the LangChain integration always attempted to patch LangChain partner  
  libraries, even if they were not available.
- langchain: Resolves an issue where tracing `Chain.invoke()` instead of `Chain.__call__()` resulted in the an `ArgumentError` due to an argument name change for inputs between the two methods.
- langchain: Adds error handling for checking if a traced LLM or chat model is an OpenAI instance, as the `langchain_community` package does not allow automatic submodule importing.
- internal: Resolves an error regarding the remote config module with payloads missing a `lib_config` entry
- profiling: Fixes a bug that caused the HTTP exporter to crash when attempting to serialize tags.
- grpc: Resolves segfaults raised when `grpc.aio` interceptors are registered
- Code Security (IAST): Fixes an issue with AES functions from the pycryptodome package that caused the application to crash and stop.
- Code Security: Ensures that when tainting the headers of a Flask application, iterating over the headers (i.e., with `headers.items()`) does not duplicate them.
- Vulnerability Management for Code-level (IAST): Some native exceptions were not being caught correctly by the python tracer. This fix removes those exceptions to avoid fatal error executions.
- kafka: Resolves an issue where an empty message list returned from consume calls could cause crashes in the Kafka integration. Empty lists from consume can occur when the call times out.
- logging: Resolves an issue where `tracer.get_log_correlation_context()` incorrectly returned a 128-bit trace_id even with `DD_TRACE_128_BIT_TRACEID_LOGGING_ENABLED` set to `False` (the default), breaking log correlation. It now returns a 64-bit trace_id.
- profiling: Fixes a defect where the deprecated path to the Datadog span type was used by the profiler.
- Profiling: Resolves an issue where the profiler was forcing `protobuf` to load in injected environments,  
  causing crashes in configurations which relied on older `protobuf` versions. The profiler will now detect when injection is used and try loading with the native exporter. If that fails, it will self-disable rather than loading protobuf.
- pymongo: Resolves an issue where the library raised an error in `pymongo.pool.validate_session`
- ASM: Resolves an issue where lfi attack on request path was not always detected with `flask` and `uwsgi`.
- ASM: Removes non-required API security metrics.
- instrumentation: Fixes crashes that could occur in certain integrations with packages that use non-integer components in their version specifiers


---

## 2.8.5


### Known Issues

- Code Security: Security tracing for the `builtins.open` function is experimental and may not be stable. This aspect is not replaced by default.
- grpc: Tracing for the `grpc.aio` clients and servers is experimental and may not be stable. This integration is now disabled by default.

### Bug Fixes

- fix(grpc): This fix a bug in the grpc.aio support specific to streaming responses.
- RemoteConfig: This fix resolves an issue where remote config did not work for the tracer when using an agent that would add a flare item to the remote config payload. With this fix, the tracer will now correctly pull out the lib_config we need from the payload in order to implement remote config changes properly.


---

## 2.8.4


### Bug Fixes

- telemetry: This fix resolves an issue when using `pytest` + `gevent` where the telemetry writer was eager initialized by `pytest` entrypoints loading of our plugin causing a potential dead lock.


---

## 2.7.10

### Bug Fixes

- Code Security: This fix solves an issue with fstrings where formatting was not applied to int parameters
- logging: This fix resolves an issue where `tracer.get_log_correlation_context()` incorrectly returned a 128-bit trace_id even with `DD_TRACE_128_BIT_TRACEID_LOGGING_ENABLED` set to `False` (the default), breaking log correlation. It now returns a 64-bit trace_id.
- profiling: Fixes a defect where the deprecated path to the Datadog span type was used by the profiler.

---

## 2.8.3


### Bug Fixes

- Code Security: This fix solves an issue with fstrings where formatting was not applied to int parameters
- logging: This fix resolves an issue where `tracer.get_log_correlation_context()` incorrectly returned a 128-bit trace_id even with `DD_TRACE_128_BIT_TRACEID_LOGGING_ENABLED` set to `False` (the default), breaking log correlation. It now returns a 64-bit trace_id.
- profiling: Fixes a defect where the deprecated path to the Datadog span type was used by the profiler.


---

## 2.6.12


### Bug Fixes

- Code Security: This fix solves an issue with fstrings where formatting was not applied to int parameters


---

## 2.8.2


### Bug Fixes

- tracing: This fix resolves an issue where sampling rules were not matching correctly on float values that had a 0 decimal value. Sampling rules now evaluate such values as integers.

- langchain: This fix resolves an issue where the LangChain integration always attempted to patch LangChain partner  
  libraries, even if they were not available.

- langchain: This fix resolves an issue where tracing `Chain.invoke()` instead of `Chain.__call__()` resulted in the an `ArgumentError` due to an argument name change for inputs between the two methods.

- langchain: This fix adds error handling for checking if a traced LLM or chat model is an OpenAI instance, as the langchain_community package does not allow automatic submodule importing.

- internal: This fix resolves an error regarding the remote config module with payloads missing a `lib_config` entry

- profiling: fix a bug that caused the HTTP exporter to crash when attempting to serialize tags.

- grpc: Resolves segfaults raised when grpc.aio interceptors are registered

- Code Security: Ensure that when tainting the headers of a Flask application, iterating over the headers (i.e., with <span class="title-ref">headers.items()</span>) does not duplicate them.


---

## 2.7.9


### Bug Fixes

- internal: This fix resolves an error regarding the remote config module with payloads missing a `lib_config` entry
- grpc: Resolves segfaults raised when grpc.aio interceptors are registered
- Code Security: Ensure that when tainting the headers of a Flask application, iterating over the headers (i.e., with <span class="title-ref">headers.items()</span>) does not duplicate them.
- pymongo: this resolves an issue where the library raised an error in `pymongo.pool.validate_session`


---

## 2.6.11


### Bug Fixes

- internal: This fix resolves an error regarding the remote config module with payloads missing a `lib_config` entry
- Code Security: Ensure that when tainting the headers of a Flask application, iterating over the headers (i.e., with <span class="title-ref">headers.items()</span>) does not duplicate them.
- pymongo: this resolves an issue where the library raised an error in `pymongo.pool.validate_session`


---

## 2.8.1


### New Features

- Code Security: to enable IAST in the application, you had to start it with the command `ddtrace-run [your-application-run-command]` so far. Now, you can also activate IAST with the `patch_all` function.

### Bug Fixes

- Code Security: fix setting the wrong source on map elements tainted from <span class="title-ref">taint_structure</span>.
- Code Security: Fixes an issue where the AST patching process fails when the origin of a module is reported as None, raising a `FileNotFoundError`.
- CI Visibility: fixes an issue where tests were less likely to be skipped due to ITR skippable tests requests timing out earlier than they should
- Code Security: Fixed an issue with AES functions from the pycryptodome package that caused the application to crash and stop.
- kafka: This fix resolves an issue where an empty message list returned from consume calls could cause crashes in the Kafka integration. Empty lists from consume can occur when the call times out.
- ASM: This fix removes unrequired API security metrics.
- instrumentation: fixes crashes that could occur in certain integrations with packages that use non-integer components in their version specifiers

---

## 2.7.8


### Bug Fixes

- Code Security: fix setting the wrong source on map elements tainted from <span class="title-ref">taint_structure</span>.
- Code Security: Fixes an issue where the AST patching process fails when the origin of a module is reported as None, raising a `FileNotFoundError`.
- CI Visibility: fixes an issue where tests were less likely to be skipped due to ITR skippable tests requests timing out earlier than they should
- Code Security: Fixed an issue with AES functions from the pycryptodome package that caused the application to crash and stop.
- ASM: This fix removes unrequired API security metrics.
- instrumentation: fixes crashes that could occur in certain integrations with packages that use non-integer components in their version specifiers

---

## 2.6.10


### Bug Fixes

- ASM: This fix resolves an issue where django login failure events may send wrong information of user existence.
- Code Security: fix setting the wrong source on map elements tainted from <span class="title-ref">taint_structure</span>.
- datastreams: Changed DSM processor error logs to debug logs for a statement which is retried. If all retries fail, the stack trace is included
- Code Security: Fixes an issue where the AST patching process fails when the origin of a module is reported as None, raising a `FileNotFoundError`.
- CI Visibility: fixes an issue where tests were less likely to be skipped due to ITR skippable tests requests timing out earlier than they should
- internal: This fix resolves an issue where importing the `ddtrace.contrib.botocore.services` module would fail raising an ImportError
- starlette: Fix a bug that crashed background tasks started from functions without a <span class="title-ref">\_\_name\_\_</span> attribute
- Code Security: Fixed an issue with AES functions from the pycryptodome package that caused the application to crash and stop.
- Code Security: This fix addresses an issue where tainting objects may fail due to context not being created in the current span.
- Code Security: Some native exceptions were not being caught correctly by the python tracer. This fix remove those exceptions to avoid fatal error executions.
- ASM: This fix removes unrequired API security metrics.
- structlog: Fixes error where multiple loggers would duplicate processors. Also adds processors injection when resetting to defaults.

---

## 2.8.0

### Prelude

tracing: This release adds support for lazy sampling, essentially moving when we make a sampling decision for a trace to the latest possible moment. These include the following: 1. Before encoding a trace chunk to be sent to the agent 2. Before making an outgoing request via HTTP, gRPC, or a DB call for any automatically instrumented integration 3. Before running `os.fork()` For most users this change shouldn't have any impact on their traces, but it does allow for more flexibility in sampling (see `features` release note). It should be noted that if a user has application egress points that are not automatically instrumented, to other Datadog components (downstream instrumented services, databases, or execution context changes), and rely on the Python tracer to make the sampling decision (don't have an upstream service doing this), they will need to manually run the sampler for those traces, or use `HttpPropagator.inject()`. For more information please see the following: <https://ddtrace.readthedocs.io/en/stable/advanced_usage.html#distributed-tracing> <https://ddtrace.readthedocs.io/en/stable/advanced_usage.html#tracing-context-management>

### New Features

- DSM: Adds base64 format for encoding and decoding DSM context hash.
- botocore: adds dsm payload size stats for botocore messaging services of kinesis, sqs and sns.
- botocore: Adds support to the bedrock integration for tagging input and output messages.
- langchain: This introduces support for `langchain==0.1.0`. Note that this does not have tracing support for deprecated langchain operations. Please follow the langchain upgrade [guide](https://python.langchain.com/docs/changelog/core) or the langchain integration :ref: <span class="title-ref">docs\<langchain\></span> to enable full tracing support.
- dramatiq: Adds automatic tracing of the `dramatiq` library.
- tracing: Added support for lazy sampling, the benefit of which is the ability to make a sampling decision using `DD_TRACE_SAMPLING_RULES` based on any span attribute (service, resource, tags, name)regardless of when the value for the attribute is set. This change is particularly beneficial for sampling on tags, since the vast majority of tags are set after the span is created. Since sampling was previously done at span creation time, this meant that those tags could not be used for sampling decisions.
- openai: Adds support for tagging streamed responses for completion and chat completion endpoints.
- profiling: implement an experimental stack sampling feature, which can be enabled by setting `DD_PROFILING_STACK_V2_ENABLED=true`. This new sampler should resolve segfault issues on Python 3.11 and later, while also decreasing the latency contribution of the profiler in many situations, and also improving the accuracy of stack-sampling data. This feature is currently only available on Linux using CPython 3.8 or greater. Requires `DD_PROFILING_EXPORT_LIBDD_ENABLED=true` to be set.
- botocore: Changes botocore aws kinesis contrib to set DSM pathway using extracted DSM context, if found, instead of always using a new pathway with default context.
- kafka: Adds tracing and DSM support for `confluent_kafka.Consumer.consume()`. Previously only <span class="title-ref">confluent_kafka.Consumer.poll</span> was instrumented.

### Deprecation Notes

- tracing: Deprecates support for `ddtrace.contrib.asyncio.AsyncioContextProvider`. ddtrace fully support tracing across asyncio tasks. Asyncio no longer requires additional configurations.
- tracing: `tracer.sampler` is deprecated and will be removed in the next major version release. To manually sample please call `tracer.sample` instead.
- gevent: Deprecates `ddtrace.contrib.gevent.provider.GeventContextProvider`. Drops support for <span class="title-ref">gevent\<20.12.0</span> and <span class="title-ref">greenlet\<1.0</span>.

### Bug Fixes

- Vulnerability Management for Code-level (IAST): Some native exceptions were not being caught correctly by the python tracer. This fix remove those exceptions to avoid fatal error executions.

- otel: Ensures that the last datadog parent_id is added to w3c distributed tracing headers generated by the OpenTelemetry API.
- ASM: This fix resolves an issue where a valid user may trigger a failed login event.
- ASM: always clear the DDWaf context at the end of the span to avoid gc-induced latency spikes at the end of some requests.
- ASM: This fix resolves an issue where django login failure events may send wrong information of user existence.
- CI Visibility: fixes an issue where git author or committer names containing commas (eg: "Lastname, Firstname") would not work (and log an error) due to the use of comma as a separator.
- propagation: This fix resolves an issue where the sampling decision-maker tag in tracestate propagation headers was clobbered by a default value.
- datastreams: Changed DSM processor error logs to debug logs for a statement which is retried. If all retries fail, the stack trace is included
- internal telemetry: Ensures heartbeat events are sent at regular intervals even when no other events are being sent.
- Fix an incompatibility between the handling of namespace module imports and parts of the functionalities of the standard library importlib module.
- internal: This fix resolves an issue where importing the `ddtrace.appsec._iast._patches` module would fail raising an ImportError
- internal: This fix resolves an issue where importing the `ddtrace.internal.peer_service` module would fail raising an ImportError
- langchain: Ensures langchain vision APIs are correctly instrumented
- Fix for the declaration of dependencies for the package.
- internal: This fix resolves an issue where importing the `ddtrace.contrib.botocore.services` module would fail raising an ImportError
- profiling: handle unexpected stack data to prevent the profiler from stopping.
- starlette: Fix a bug that crashed background tasks started from functions without a <span class="title-ref">\_\_name\_\_</span> attribute
- ASM: This fix resolves an issue where the asgi middleware could crash with a RuntimeError "Unexpected message received".
- ASM: This fix resolves an issue with Flask instrumentation causing CPU leak with ASM, API Security and Telemetry enabled.
- Vulnerability Management for Code-level (IAST): Addresses an issue where the IAST native module was imported even though IAST was not enabled.
- Vulnerability Management for Code-level (IAST): This fix addresses an issue where tainting objects may fail due to context not being created in the current span.
- Vulnerability Management for Code-level (IAST): This fix addresses an issue where AST patching would generate code that fails to compile, thereby preventing the application from starting correctly.
- Vulnerability Management for Code-level (IAST): This fix addresses AST patching issues where other subscript operations than `Load` were being unintentionally patched, leading to compilation errors for the patched module.
- Vulnerability Management for Code-level (IAST): Fixes an issue where an atexit handler could lead to a segmentation fault.
- Vulnerability Management for Code-level (IAST): This fix addresses an issue where a vulnerability would be reported at line 0 if we couldn't extract the proper line number, whereas the default line number should be -1.
- kafka: This fix resolves an issue where `None` messages from confluent-kafka could cause crashes in the Kafka integration.
- appsec: This fix resolves an issue in which the library attempted to finalize twice a context object used by the Application Security Management product.
- tracing: Removes `allow_false` argument from ddtrace samplers. `allow_false` allows datadog samplers to return a value that differs from the sampling decision, this behavior is not supported.
- profiling: This fixes a `free(): invalid pointer` error which would arise as a result of incorrectly linking the C++ runtime.
- starlette: Ensures correct URL tag is set for starlette v0.34.0 and above.
- structlog: Fixes error where multiple loggers would duplicate processors. Also adds processors injection when resetting to defaults.


---

## 2.7.7

### Bug Fixes

- ASM: This fix resolves an issue where django login failure events may send wrong information of user existence.
- datastreams: Changed DSM processor error logs to debug logs for a statement which is retried.  If all retries fail, the stack trace is included
- internal: This fix resolves an issue where importing the ``ddtrace.internal.peer_service`` module would fail raising an ImportError
- starlette: Fix a bug that crashed background tasks started from functions without a `__name__` attribute
- Vulnerability Management for Code-level (IAST): This fix addresses an issue where tainting objects may fail due to context not being created in the current span.
- Vulnerability Management for Code-level (IAST): Some native exceptions were not being caught correctly by the python tracer.
  This fix remove those exceptions to avoid fatal error executions.
- kafka: This fix resolves an issue where an empty message list returned from consume calls could cause crashes in the Kafka integration.
  Empty lists from consume can occur when the call times out.


---

## 2.7.6


### Bug Fixes

- Profiling: This fix resolves an issue where the profiler was forcing protobuf to load in injected environments,
  causing crashes in configurations which relied on older protobuf versions. The profiler will now detect when injection is used and try loading with the native exporter. If that fails, it will self-disable rather than loading protobuf.


---

## 2.7.5


### New Features

- kafka: Adds tracing and DSM support for `confluent_kafka.Consumer.consume()`. Previously only <span class="title-ref">confluent_kafka.Consumer.poll</span> was instrumented.

### Bug Fixes

- ASM: always clear the DDWaf context at the end of the span to avoid gc-induced latency spikes at the end of some requests.
- internal: This fix resolves an issue where importing the `ddtrace.contrib.botocore.services` module would fail raising an ImportError
- setuptools_scm version: Updates the setuptools_scm versioning method to "guess-next-dev" from "release-branch-semver", which was affecting the CI
- structlog: Fixes error where multiple loggers would duplicate processors. Also adds processors injection when resetting to defaults.


---

## 2.6.9


### Bug Fixes

- propagation: This fix resolves an issue where the sampling decision-maker tag in tracestate propagation headers was clobbered by a default value.
- langchain: Ensures langchain vision APIs are correctly instrumented
- ASM: This fix resolves an issue where the asgi middleware could crash with a RuntimeError "Unexpected message received".
- kafka: This fix resolves an issue where `None` messages from confluent-kafka could cause crashes in the Kafka integration.


---

## v2.6.0

### Upgrade Notes

- CI Visibility: `DD_CIVISIBILITY_ITR_ENABLED` now defaults to true, and the Datadog API (configured via the Datadog dashboard) now determines whether code coverage and test skipping are enabled.
- CI Visibility: the CI Visibility service is no longer enabled when the initial query to the Datadog test service settings API fails due to a 403 status code.

### New Features

- botocore: Adds optional feature to propagate context between producers and consumers for AWS SQS, AWS SNS, and AWS Kinesis via <span class="title-ref">DD_BOTOCORE_PROPAGATION_ENABLED</span> environment variable. Adds optional feature to disable tracing of AWS SQS <span class="title-ref">poll()</span> operation and AWS Kinesis 'get_records()' operation when no data is consumed via <span class="title-ref">DD_BOTOCORE_EMPTY_POLL_ENABLED</span> environment variable.

- tracing: Adds new tag <span class="title-ref">python_main_package</span> containing the name of the main package of the application. profiling: Adds new tag <span class="title-ref">python_main_package</span> containing the name of the main package of the application.

- ASM: API Security schema collection is now officially supported for Django, Flask and FastAPI. It can be enabled in the tracer using environment variable DD_API_SECURITY_ENABLED=true It will only be active when ASM is also enabled.

- elasticsearch: This allows custom tags to be set on Elasticsearch spans via the Pin interface.

- botocore: This introduces tracing support for bedrock-runtime operations.
  See [the docs](https://ddtrace.readthedocs.io/en/stable/integrations.html#botocore) for more information.

- datastreams: this change adds kombu auto-instrumentation for datastreams monitoring. tracing: this change adds the `DD_KOMBU_DISTRIBUTED_TRACING` flag (default `True`)

- Vulnerability Management for Code-level (IAST): Add support for CMDi in langchain.

- botocore: Add the ability to inject trace context into the input field of botocore stepfunction start_execution and start_sync_execution calls.

- Removes another place where we always load instrumentation telemetry, even if it is disabled

- tracing: This introduces the ability to disable tracing at runtime based on configuration values sent from the Datadog frontend. Disabling tracing in this way also disables instrumentation telemetry.

- tracing: Adds support for remote configuration of `DD_TRACE_HEADER_TAGS`

- tracing: Add support for remote configuration of trace-logs correlation.

- grpc/grpc_aio: reports the available target host in client spans as `network.destination.ip` if only an IP is available, `peer.hostname` otherwise.

- span: Adds a public api for setting span links

- starlette,fastapi: Trace background tasks using span links

### Bug Fixes

- ASM: This fix resolves an issue where an exception would be logged while parsing an empty body JSON request.

- CI Visibility: fixes an issue where coverage data for suites could be lost for long-running test sessions, reducing the possibility of skipping tests when using the Intelligent Test Runner.

- IAST: Don't split AST Assign nodes since it's not needed for propagation to work.

- ASM: This fix resolves an issue where suspicious request blocking on request data was preventing API Security to collect schemas in FastAPI, due to route not being computed.

- ASM: This fix resolves an issue where ASM custom blocking actions with a redirect action could cause the server to drop the response.

- Fixed an incompatible version requirements for one of the internal dependencies that could have caused an exception to be raised at runtime with Python 3.12.

- data_streams: This change fixes a bug leading to lag being reported as 1 offset instead of 0 offsets.

- IAST: fixes import overhead when IAST is disabled.

- Fix an incomplete support for pkg_resouces that could have caused an exception on start-up.

- Fix an issue that caused an exception to be raised when trying to access resource files via `pkg_resources`.

- Fix for an import issue that caused the pytest plugin to fail to properly initialize a test session and exit with an import exception.

- openai: This fixes a bug that prevents logs from being correlated with traces in the Datadog UI.

- langchain: This fixes a bug that prevents logs from being correlated with traces in the Datadog UI.

- openai: This fix resolves an issue where an internal OpenAI method <span class="title-ref">SyncAPIClient.\_process_response</span>
  was not being patched correctly and led to to an AttributeError while patching.

- profiling: handle a potential system error that may be raised when running a Celery-based application with CPython 3.11.

- Fixed an issue that could have caused an exception as a result of a concurrent access to some internal value cache.

- tracing: Ensures span links are serialized with the expected traceflag when `DD_TRACE_API_VERSION=v0.4`

- ASM: This fix resolves an issue where IP Headers configured by the user in the environment could not work for frameworks handling requests with case insensitive headers like FastAPI.

- Vulnerability Management for Code-level (IAST): Fixes a bug in the `str` aspect where encoding and errors arguments were not honored correctly.

- Vulnerability Management for Code-level (IAST): Fix an unhandled ValueError in `ast_function` thrown in some cases (i.e. Numpy arrays when converted to bool).

- opentelemetry: Ensures that span links are serialized in a json-compatible representation.

- Pin importlib_metadata to 6.5.0 to avoid its issue 455 (<https://github.com/python/importlib_metadata/issues/455>).

- profiler: Fixes a sigabrt when shutdown occurs during an upload

- otel: Ensures all otel sampling decisions are consistent with Datadog Spans. This prevents otel spans in a distrbuted trace from being sampled differently than Datadog spans in the same trace.

- tracing: Fix an issue where remote configuration values would not be reverted when unset in the UI.

- tracing: Ensures hostnames are reported in statsd metrics if `DD_TRACE_REPORT_HOSTNAME=True` (default value is `False`).

### Other Changes

- setup: pins the default macOS deployment target to 10.14.
- tracing: Updates the default value of `DD_TRACE_PROPAGATION_STYLE` from `tracecontext,datadog` to `datadog,tracecontext`. With this change w3c tracecontext headers will be parsed before datadog headers. This change is backwards compatible and should not affect existing users.

---

## v2.5.0

### New Features

- aiohttp: add <span class="title-ref">split_by_domain</span> config to split service name by domain
- CI Visibility: Adds code coverage lines covered tag for `pytest` and `unittest`.
- aiohttp: Adds http.route tag to `aiohttp.request` spans.
- bottle: Adds http.route tag to `bottle.request` spans.
- falcon: Adds http.route tag to `falcon.request` spans.
- molten: Adds http.route tag to `molten.request` spans.
- Adds distributed tracing for confluent-kafka integration. Distributed tracing connects Kafka consumer spans with Kafka producer spans within the same trace if a message is valid. To enable distributed tracing, set the configuration: `DD_KAFKA_DISTRIBUTED_TRACING_ENABLED=True` for both the consumer and producer service.
- ASM: This introduces (experimental) api security support for fastAPI. Flask and Django were already supported in 2.4.0. Support schema computation on all addresses (requests and responses) and scanner support for pii, credentials and payment data.
- CI Visibility: introduces a CI visibility-specific logger (enabled for the `pytest` plugin), enabled by setting the `DD_CIVISIBILITY_LOG_LEVEL` environment variable (with the same level names as Python logging levels).
- CI Visibility: allows for waiting for the git metadata upload to complete before deciding whether or not to enable coverage (based on API response).
- Further lazy loads telemetry_writer so that it is not running when explicitly disabled. Users must explicitly set "DD_INSTRUMENTATION_TELEMETRY_ENABLED=false".
- tracer: Add support for remotely configuring trace tags.

### Bug Fixes

- loguru: Ensures log correlation is enabled when the root logger is initialized. Previously, log correlation was only enabled when a new sink was added.
- Fix compatibility with other tools that try to infer the type of a Python object at runtime.
- tracing: Fixes a bug that prevents span links from being visualized in the Datadog UI.
- tracing: Resolves span encoding errors raised when span links do not contain expected types
- ASM: This fix resolves an issue where custom event boolean properties were not reported as <span class="title-ref">true</span> and <span class="title-ref">false</span> like other tracers but as <span class="title-ref">True</span> and <span class="title-ref">False</span>.
- Vulnerability Management for Code-level (IAST): Ensure that Cookies vulnerabilities report only the cookie name.
- langchain: This fix resolves an `get_openai_token_cost_for_model` import error in langhcain version 0.0.351 or later.
- ASM: This fix resolves an issue where IAST could cause circular dependency at startup.
- tracing: Ensures all fields in `ddtrace.context.Context` are picklable.
- pytest: This fix resolves an issue where the <span class="title-ref">--no-cov</span> flag did not take precedence over the <span class="title-ref">--cov</span> flag when deciding whether to report code coverage on spans.
- rq: Fixed a bug where the RQ integration would emit a warning when setting `job.status` span tag.

---

## v2.4.0

### Upgrade Notes

- <div id="remove-unsupported-pylons">

  This removes the `pylons` integration, which does not support Python 3.

  </div>

### Deprecation Notes

- aioredis: The aioredis integration is deprecated and will be removed in a future version. As an alternative to the aioredis integration, you can use the redis integration with redis\>=4.2.0.

### New Features

- ASM: dependency telemetry metrics now will only report dependencies actually in use (imported) and will also report new imported modules periodically.

- ASM: This introduces Threat Monitoring and Blocking on FastAPI.
  - IP Blocking and all input addresses are supported on requests and responses

  \- Custom Blocking This does not contain user blocking specific features yet.

- tracing: Introduces support for OpenTracing Baggage Items with HTTP Propagation. Enable this support by `DD_TRACE_PROPAGATION_HTTP_BAGGAGE_ENABLED=true`. The `Context._set_baggage_item` and `Context._get_baggage_item` internal methods are provided for manual modifications to the Baggage Items. These API changes are subject to change.

- dynamic instrumentation: Add support for more built-in container types, such as `defaultdict`, `frozenset`, `OrderedDict` and `Counter`.

- Vulnerability Management for Code-level (IAST): Adds Python 3.12 compatibility

- Optionally lazy loads and disables Instrumentation Telemetry. Users must explicitly set "DD_INSTRUMENTATION_TELEMETRY_ENABLED=false".

- tracer: Add support for remotely setting the trace sample rate from the Datadog UI. This functionality is enabled by default when using `ddtrace-run` and library injection. To enable it when using the library manually, use `ddtrace.config.enable_remote_config()`.

### Bug Fixes

- tracer: tag spans that have been sampled due to an Agent sampling configuration.
- lambda: This change disables the use of `multiprocessing.queue` in Lambda, because it is not supported in Lambda
- langchain: This fix resolves a crash that could occur during embedding when no embeddings are found.
- Fix a regression with the support for gevent that could have occurred if some products, like ASM, telemetry, were enabled.
- kafka: Resolves `TypeError` raised by serializing producers and deserializing consumers when the `message.key` tag is set on spans.
- dynamic instrumentation: Fix an issue that caused the instrumented application to fail to start if a non-standard module was imported.
- openai: This fix resolves an issue where tagging image inputs in the chat completions endpoint resulted in attribute errors.
- openai: This fix resolves an issue where requesting raw API responses from openai\>=1.0 resulted in attribute errors while tagging.
- profiling: Fix an issue that prevented threading locks from being traced when using gevent.
- profiling: Fix a segmentation fault with CPython 3.12 when sampling thread stacks.
- pylibmc: Fixes an issue where using `ddtrace-run` or `ddtrace.patch_all()` with `DD_TRACE_ENABLED=False` would break with get, gets, and get_multi operations on pylibmc Clients.
- tracing: This fix resolves an issue where concurrent mutations to the `context._meta` dict caused <span class="title-ref">RuntimeError: dictionary changed size during iteration</span>.
- django: Resolves `AttributeError` raised by traced `StreamingHttpResponse`.
- Vulnerability Management for Code-level (IAST): This fix resolves an issue where certain aspects incorrectly expected at least one argument, leading to an IndexError when none were provided. The solution removes this constraint and incorporates regression tests for stability assurance.
- Vulnerability Management for Code-level (IAST): Cookies vulnerabilities are only reported if response cookies are insecure.
- Vulnerability Management for Code-level (IAST): Fix propagation error on `.format` string method.
- requests: Updates the resource names of `requests.requests` spans to include the method and path of the request.
- propagation: This fix resolves an issue where a `Context` generated from extracted headers could lack a span_id or trace_id, leading `SpanLink` encoding errors.
- psycopg: This fix resolves an issue where a circular import of the psycopg library could cause a crash during monkeypatching.
- psycopg: This fix resolves an issue where exceptions originating from asynchronous Psycopg cursors were not propagated up the call stack.
- redis: This fix resolves an issue where the yaaredis and aredis integrations imported code from the redis integration, causing a circular import error.
- tracing: Resolves trace encoding errors raised when `DD_TRACE_API_VERSION` is set to `v0.5` and a BufferFull Exception is raised by the TraceWriter. This fix ensures span fields are not overwritten and reduces the frequency of 4XX errors in the trace agent.

### Other Changes

- tracing: Upgrades the trace encoding format to v0.5. This change improves the performance of encoding and sending spans.

---

## v2.3.0

### New Features

- propagation: 128-bit trace ids are now used by default for propagation. Previously the default was 64-bit. This change is backwards compatible with tracers that still use 64-bit trace ids and should not cause any breaking behavior.
- Adds DSM `pathway.hash` tag to spans when DSM is enabled. This allows traces from the instrumented service to show up in the DSM traces tab.
- propagation: When the tracer is configured to extract and inject `tracecontext`, the tracer will propagate the tracestate values from other vendors so long as the traceparent trace-id matches the first found trace context, regardless of propagator configuration order. To disable this behavior `DD_TRACE_PROPAGATION_EXTRACT_FIRST=true` can be set.
- opentelemetry: Map reserved OpenTelemetry attributes to Datadog span model.
- opentelemetry: datadog operation name from semantic conventions
- propagation: If a valid context is extracted from headers, and the following extracted trace context's `trace_id`s do not match the valid context's, then add a span link to the root span to represent the broken propagation.
- tracing: This change treats spans that terminated with `sys.exit(0)` as successful non-error spans.
- tracing: This introduces the `DD_TRACE_SPAN_TRACEBACK_MAX_SIZE` environment variable, allowing the maximum size of tracebacks included on spans to be configured.

### Bug Fixes

- CI Visibility: fixes the fact that the GITHUB_SERVER_URL environment variable was not being sanitized for credentials
- dynamic instrumentation: Needs to update the pubsub instance when the application forks because the probe mechanism should run in the child process. For that, DI needs the callback as the method of an instance of Debugger, which lives in the child process.
- CI Visibility: Fixes an issue where a `ValueError` was raised when using different path drives on Windows
- Fixes an issue where ddtrace could not be installed from source when using `setuptools>=69` due to a change in the license field.
- tracing: Fixes an issue where the thread responsible for sending traces is killed due to concurrent dictionary modification.
- structlog: Fixes `TypeError` raised when ddtrace log processor is configured with a tuple
- Vulnerability Management for Code-level (IAST): Generates cookies vulnerabilities report if IAST is enabled. Before this fix, Cookies vulnerabilities were only generated if both IAST and Appsec were enabled.
- Vulnerability Management for Code-level (IAST): This fix resolves an issue where, at AST patching to replace code with IAST aspects, passing the original function/method as an extra parameter for accurate patching unintentionally triggers side effects in methods obtained from an expression (like `decode` in `file.read(n).decode()`), resulting in unexpected multiple calls to the expression (`file.read(n)` in the example).
- Vulnerability Management for Code-level (IAST): This fix eliminates some reference leaks and C-API usage when IAST reports a vulnerability and calls `get_info_frame`.
- kafka: This fix resolves an issue where calls to `confluent_kafka`'s `produce` method with `key=None` would cause an exception to be raised.
- tracing: This fix resolves an issue where ddtrace's signal handlers could cause Flask apps not to respond correctly to SIGINT.
- logging: A log handler is automatically added to the ddtrace logger upon ddtrace import, when not using ddtrace-run. This can lead to duplicate logging if users add additional loggers and do not explicitly modify the ddtrace logger. This fix adds a feature flag that can be used to toggle this behavior off `DD_TRACE_LOG_STREAM_HANDLER` which defaults to `true`.

---

## v2.2.0

### Upgrade Notes

- The `wrapt` and `psutil` packages are vendored to help users avoid building these packages if wheels were not available for a given platform. This reverses a change released in v2.0.0.

### New Features

- CI Visibility: adds ITR support for `unittest`
- CI Visibility: adds start/end line support for `pytest` test spans
- CI Visibility: adds start/end line source file data to `unittest` test spans
- aiohttp: This introduces basic tracing of streaming responses that stay open long after the <span class="title-ref">on_prepare</span> signal has been sent.
- CI Visibility: introduce pytest hooks for modifying the module, suite, and test naming logic
- CI Visibility: add support for AWS Codepipeline to CI env var gathering
- datastreams: this change adds message payload size metrics and aggregations for Kafka.
- structlog: Wraps get_logger function in order to add datadog injection processor regardless of configuration
- openai: This adds support for openai v1.
- Source Code: filters Git repo URLs from env vars and setuptools
- logbook: This introduces log correlation for the logbook library. Refer to `logbook-docs <ddtrace.contrib.logbook>` for more details.
- loguru: This introduces log correlation for the loguru library. Refer to `loguru-docs <ddtrace.contrib.loguru>` for more details.
- openai: This adds support for tagging function call arguments when using OpenAI's function calling feature.
- Adds ARM64 support for Single-Step instrumentation
- structlog: This introduces log correlation for the structlog library. Refer to `structlog-docs <ddtrace.contrib.structlog>` for more details.
- celery: Adds Python 3.11 and 3.12 support for the celery integration.

### Known Issues

- ASM: fix a body read problem on some corner case where passing empty content length makes wsgi.input.read() blocks.

### Bug Fixes

- Application Security Management (ASM): fix a body read error when `Transfer-Encoding: chunked` header is sent
- CI Visibility: fixes an issue where class-based test methods with the same name across classes would be considered duplicates, and cause one (or more) tests to be dropped from results, by adding `--ddtrace-include-class-name` as an optional flag (defaulting to false) to prepend the class name to the test name.
- CI Visibility: fixes a crash where the unittest integration would try to enable coverage when tests are run even if the Intelligent Test Runner is not enabled.
- data_streams: This fix resolves an issue where tracing would crash if a kafka client produced a message with no key or value.
- CI: fixes an issue which prevented the library from filtering user credentials for SSH Git repository URLs
- dynamic instrumentation: fix an issue that caused function probes on the same module to fail to instrument and be reported in the `ERROR` status in the UI if the module was not yet imported.
- Use a unique default service name across all the products provided by the library when one is not given via the configuration interface.
- sampling: This fix reverts a refactor which affected how the tracer handled the trace-agent's recommended trace sampling rates, leading to an unintended increase in traces sampled.
- tracing: Fixes a msgpack import error when `DD_TRACE_API` is set to `v0.5`
- fix(profiling): numeric type exception in memalloc When pushing allocation samples, an exception was being thrown due to a float being passed instead of an integer. We now cast the ceiled value to an integer.
- CI Visibility: fixes `unittest` data not being initialized properly
- CI Visibility: fixes an issue where just importing <span class="title-ref">unittest</span> enabled CIVisibility and potentially caused unexpected logs and API requests
- Vulnerability Management for Code-level (IAST): This fix addresses AST patching issues where custom functions or methods could be replaced by aspects with differing argument numbers, causing runtime errors as a result. Furthermore, it addresses a case during patching where the module is inadvertently passed as the first argument to the aspect.
- Vulnerability Management for Code-level (IAST): Fix potential string id collisions that could cause false positives with non tainted objects being marked as tainted.
- IAST: This fix resolves an issue where JSON encoder would throw an exception while encoding a tainted dict or list.
- Vulnerability Management for Code-level (IAST): This fix resolves an issue where SimpleJSON encoder would throw an exception while encoding a tainted dict or list.
- ASM: add support for psycopg2 adapt mechanism to LazyTaintList, preventing a ProgrammingError when using psycopg2 with IAST.
- tracing: This fix resolves an issue where unserializable tracer attributes caused crashes when `DD_TRACE_DEBUG` was set.
- This fix resolves an issue where `confluent_kafka`'s `SerializingProducer` and `DeserializingConsumer` classes were incorrectly patched, causing crashes when these classes are in use with Datadog patching.
- langchain: This fix resolves an issue with tagging pydantic <span class="title-ref">SecretStr</span> type api keys.
- lib injection: Fix permissions error raised when non-root users copy single step instrumentation files.
- redis: The Datadog Agent removes command arguments from the resource name. However there are cases, like compressed keys, where this obfuscation cannot correctly remove command arguments. To safeguard that situation, the resource name set by the tracer will only be the command (e.g. SET) with no arguments. To retain the previous behavior and keep arguments in the span resource, with the potential risk of some command arguments not being fully obfuscated, set `DD_REDIS_RESOURCE_ONLY_COMMAND=false`.

### Other Changes

- tags: Previously `DD_TRACE_X_DATADOG_TAGS_MAX_LENGTH` had a max limit setting of 512 characters. This change removes that limit but keeps the default at 512.

---

## v2.0.0

### Prelude

The Datadog APM Python team is happy to announce the release of v2.0.0 of ddtrace. This release drops support for Python 2.7, 3.5, and 3.6. This release adds support for Python 3.12.

<div class="important">

<div class="title">

Important

</div>

If you are on version of Python not supported by v2, we will continue to maintain the ddtrace v1 with bug fixes.

</div>

<div class="note">

<div class="title">

Note

</div>

Before upgrading to v2.0.0, we recommend users install `ddtrace~=1.20.0` and enable deprecation warnings. All removals to the library interface and environment variables in v2 were deprecated in the 1.x release line.

</div>

<div class="note">

<div class="title">

Note

</div>

The changes to environment variables apply only to the configuration of the ddtrace library and not the Datadog Agent.

</div>

#### Upgrading summary

##### Functionality changes

The default logging configuration functionality of ddtrace has been changed to avoid conflicting with application logging configurations. `DD_CALL_BASIC_CONFIG` has been removed and the ddtrace logger will log to stdout by default, or a log file as specified using `DD_TRACE_LOG_FILE`.

Setting the environment variable `DD_TRACE_PROPAGATION_STYLE='b3'`, which previously enabled `b3multi` now enables `b3 single header`. `b3 single header` still works but is deprecated for `b3`. Simplified: `b3` used to enable `b3multi`, but now enables `b3 single header` to better align with Opentelemetry's terms.

##### Removed deprecated environment variables

These environment variables have been removed. In all cases the same functionality is provided by other environment variables and replacements are provided as recommended actions for upgrading.

| Variable                                   | Replacement                                | Note                                                |
|--------------------------------------------|--------------------------------------------|-----------------------------------------------------|
| `DD_GEVENT_PATCH_ALL`                      | None                                       | `📝<remove-dd-gevent-patch-all>`                    |
| `DD_AWS_TAG_ALL_PARAMS`                    | None                                       | `📝<remove-aws-tag-all-params>`                     |
| `DD_REMOTECONFIG_POLL_SECONDS`             | `DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS`   | `📝<rename-remote-config-poll-seconds>`             |
| `DD_CALL_BASIC_CONFIG`                     | None                                       | `📝<remove-basic-config>`                           |
| `DD_TRACE_OBFUSCATION_QUERY_STRING_PATERN` | `DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP` | `📝<remove-trace-obfuscation-query-string-pattern>` |

##### Removed deprecated library interfaces

These methods and module attributes have been removed. Where the same functionality is provided by a different public method or module attribute, a recommended action is provided for upgrading. In a few limited cases, because the interface was no longer used or had been moved to the internal interface, it was removed and so no action is provided for upgrading.

| Module                            | Method/Attribute                | Note                                               |
|-----------------------------------|---------------------------------|----------------------------------------------------|
| `ddtrace.constants`               | `APPSEC_ENABLED`                | `📝<remove-appsec-private-constants>`              |
|                                   | `APPSEC_JSON`                   | `📝<remove-appsec-private-constants>`              |
|                                   | `APPSEC_EVENT_RULE_VERSION`     | `📝<remove-appsec-private-constants>`              |
|                                   | `APPSEC_EVENT_RULE_ERRORS`      | `📝<remove-appsec-private-constants>`              |
|                                   | `APPSEC_EVENT_RULE_LOADED`      | `📝<remove-appsec-private-constants>`              |
|                                   | `APPSEC_EVENT_RULE_ERROR_COUNT` | `📝<remove-appsec-private-constants>`              |
|                                   | `APPSEC_WAF_DURATION`           | `📝<remove-appsec-private-constants>`              |
|                                   | `APPSEC_WAF_DURATION_EXT`       | `📝<remove-appsec-private-constants>`              |
|                                   | `APPSEC_WAF_TIMEOUTS`           | `📝<remove-appsec-private-constants>`              |
|                                   | `APPSEC_WAF_VERSION`            | `📝<remove-appsec-private-constants>`              |
|                                   | `APPSEC_ORIGIN_VALUE`           | `📝<remove-appsec-private-constants>`              |
|                                   | `APPSEC_BLOCKED`                | `📝<remove-appsec-private-constants>`              |
|                                   | `IAST_JSON`                     | `📝<remove-appsec-private-constants>`              |
|                                   | `IAST_ENABLED`                  | `📝<remove-appsec-private-constants>`              |
|                                   | `IAST_CONTEXT_KEY`              | `📝<remove-appsec-private-constants>`              |
| `ddtrace.contrib.fastapi.patch`   | `span_modifier`                 | `📝<remove-fastapi-starlette-span-modifier>`       |
|                                   | `aggregate_resources`           | `📝<remove-fastapi-starlette-aggregate-resources>` |
| `ddtrace.contrib.starlette.patch` | `span_modifier`                 | `📝<remove-fastapi-starlette-span-modifier>`       |
|                                   | `aggregate_resources`           | `📝<remove-fastapi-starlette-aggregate-resources>` |
|                                   | `get_resource`                  | `📝<remove-fastapi-starlette-span-modifier>`       |
| `ddtrace.contrib.grpc.constants`  | `GRPC_PORT_KEY`                 | `📝<remove-grpc-port-key>`                         |
| `ddtrace.ext.cassandra`           | `ROW_COUNT`                     | `📝<remove-cassandra-row-count>`                   |
| `ddtrace.ext.mongo`               | `ROWS`                          | `📝<remove-mongo-row-count>`                       |
| `ddtrace.ext.sql`                 | `ROWS`                          | `📝<remove-sql-row-count>`                         |
| `ddtrace.filters`                 | `TraceCiVisibilityFilter`       | `📝<remove-trace-ci-visibility-filter>`            |
| `ddtrace.tracer`                  | `DD_LOG_FORMAT`                 | `📝<remove-dd-log-format>`                         |

### Upgrade Notes

- <div id="remove-dd-gevent-patch-all">

  `DD_GEVENT_PATCH_ALL` is removed. There is no special configuration necessary to make ddtrace work with gevent if using ddtrace-run.

  </div>

- <div id="remove-aws-tag-all-params">

  `DD_AWS_TAG_ALL_PARAMS` is removed. The boto/botocore/aiobotocore integrations no longer collect all API parameters by default.

  </div>

- <div id="rename-remote-config-poll-seconds">

  `DD_REMOTECONFIG_POLL_SECONDS` is removed. Use the environment variable `DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS` instead.

  </div>

- <div id="remove-appsec-private-constants">

  `APPSEC_ENABLED`, `APPSEC_JSON`, `APPSEC_EVENT_RULE_VERSION`, `APPSEC_EVENT_RULE_ERRORS`, `APPSEC_EVENT_RULE_LOADED`, `APPSEC_EVENT_RULE_ERROR_COUNT`, `APPSEC_WAF_DURATION`, `APPSEC_WAF_DURATION_EXT`, `APPSEC_WAF_TIMEOUTS`, `APPSEC_WAF_VERSION`, `APPSEC_ORIGIN_VALUE`, `APPSEC_BLOCKED`, `IAST_JSON`, `IAST_ENABLED`, `IAST_CONTEXT_KEY` are removed. This should not affect existing code as these deprecated ASM constants were meant for private use only.

  </div>

- <div id="remove-fastapi-starlette-span-modifier">

  `ddtrace.contrib.starlette.get_resource`, `ddtrace.contrib.starlette.span_modifier`, and `ddtrace.contrib.fastapi.span_modifier` are removed. The starlette and fastapi integrations now provide the full route and not just the mounted route for sub-applications.

  </div>

- <div id="remove-fastapi-starlette-aggregate-resources">

  `ddtrace.contrib.starlette.config['aggregate_resources']` and `ddtrace.contrib.fastapi.config['aggregate_resources']` are removed. The starlette and fastapi integrations no longer have the option to `aggregate_resources`, as it now occurs by default.

  </div>

- <div id="remove-grpc-port-key">

  `ddtrace.contrib.grpc.constants.GRPC_PORT_KEY` is removed. Use `ddtrace.ext.net.TARGET_PORT` instead.

  </div>

- <div id="remove-cassandra-row-count">

  `ddtrace.ext.cassandra.ROW_COUNT` is removed. Use `ddtrace.ext.db.ROWCOUNT` instead.

  </div>

- <div id="remove-mongo-row-count">

  `ddtrace.ext.mongo.ROW_COUNT` is removed. Use `ddtrace.ext.db.ROWCOUNT` instead.

  </div>

- <div id="remove-sql-row-count">

  `ddtrace.ext.sql.ROW_COUNT` is removed. Use `ddtrace.ext.db.ROWCOUNT` instead.

  </div>

- <div id="remove-trace-ci-visibility-filter">

  `ddtrace.filters.TraceCiVisibilityFilter` is removed.

  </div>

- <div id="remove-dd-log-format">

  `ddtrace.tracer.DD_LOG_FORMAT` is removed. As an alternative, please follow the log injection formatting as provided in the [log injection docs](https://ddtrace.readthedocs.io/en/stable/advanced_usage.html#update-log-format).

  </div>

- <div id="remove-basic-config">

  `DD_CALL_BASIC_CONFIG` is removed. There is no special configuration necessary to replace `DD_CALL_BASIC_CONFIG`. The ddtrace logger will log to stdout by default or additionally to a file specified by `DD_TRACE_LOG_FILE`.

  </div>

- <div id="remove-trace-obfuscation-query-string-pattern">

  `DD_TRACE_OBFUSCATION_QUERY_STRING_PATTERN` is removed. Use `DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP` instead.

  </div>

### New Features

- Adds support for Python 3.12.

### Known Issues

- aiohttp: Python 3.12 is not supported.
- aiohttp-jinja: Python 3.12 is not supported.
- aiobotocore: Python 3.12 is not supported.
- asm: IAST for Python 3.12 is not supported.
- flask-caching: Python 3.12 is not supported.
- openai/langchain: Python 3.12 is not supported.
- opentelemetry-api: Python 3.12 is not supported.
- opentracing: Python 3.12 is not supported.
- pyramid: Python 3.12 is not supported.
- pynamodb: Python 3.12 is not supported.
- redis/redis-py-cluster: Python 3.12 is not supported.

---

## v1.20.0

### Prelude

Vulnerability Management for Code-level (IAST) is now available in private beta. Use the environment variable `DD_IAST_ENABLED=True` to enable this feature.

### New Features

- ASM: This introduces support for custom blocking actions of type redirect_request.
- data_streams: Adds public api `set_produce_checkpoint` and `set_consume_checkpoint`

### Bug Fixes

- kafka: Resolves an issue where traced kafka connections were assigned a default timeout of 1 second. The default timeout in [Consumer.poll(...)](https://docs.confluent.io/platform/current/clients/confluent-kafka-python/html/index.html#confluent_kafka.Consumer.poll) should be None.
- openai: This fix resolves an issue where errors during streamed requests resulted in unfinished spans.

---

## v1.19.0

### New Features

- Adds the <span class="title-ref">db.row_count</span> tag to redis and other redis-like integrations. The tag represents the number of returned results.
- CI Visibility: adds test level visibility for [unittest](https://docs.python.org/3/library/unittest.html)
- ASM: Adds detection of insecure cookie vulnerabilities on responses.
- ASM: This introduces trusted IPs capabilities in the tracer, to allow specific IPs not to be blocked by ASM but still be monitored.
- ASM: This introduces a new capability to configure the blocking response of ASM. Users can change the default blocking response behavior or create new custom actions. Configuration of a custom blocking page or payload can still be provided by using <span class="title-ref">DD_APPSEC_HTTP_BLOCKED_TEMPLATE_JSON</span> and <span class="title-ref">DD_APPSEC_HTTP_BLOCKED_TEMPLATE_HTML</span> to change the static files used for the response body. The action block, that can be defined in the static rule file or via remote configuration, allows now to create new custom blocking actions with any status code for the response.
- The aiopg and aiomysql integrations no longer set the sql.query tag on query spans. This tag duplicated the value captured by the span resource. Users who want to send this query unobfuscated can use the tracer API to set tags on the query span.
- data_streams: Starts tracking Kafka lag in seconds.
- kafka: Adds support for the Kafka serializing producer and deserializing consumer.
- profiling: allow individual collectors to be disabled.
- tracing: This change introduces the `allow_false` keyword argument to `BaseSampler.sample()`, which defaults to `True`. `allow_false` controls the function's return value. If `allow_false` is `False`, the function will always return `True` regardless of the sampling decision it made. This is useful when `sample` is called only for its side effects, which can include setting span tags.

### Known Issues

- There are known issues configuring python's builtin multiprocessing library when ddtrace is installed. To use the multiprocessing library with ddtrace ensure `DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE` is set to `True`.
- When running setup.py extensions with the CMake parameter "-j", it could potentially raise an out-of-memory error. If someone wants to expedite the ddtrace installation, they should manually set the "CMAKE_BUILD_PARALLEL_LEVEL" environment variable.

### Bug Fixes

- ASM: avoid potentially unneeded import of the IAST native module.

- ASM: avoid potentially unneeded import of the IAST native module if setup doesn't build extensions correctly.

- data_streams: This fix resolves an issue where data stream context propagation would not propagate via SNS if raw message delivery was enabled.

- dynamic instrumentation: function duration measurements are now reported in milliseconds to match the expectation from the UI.

- dynamic instrumentation: fixed an issue that prevented line probes from being injected in some finally blocks.

- dynamic instrumentation: Fixed the programmatic API to ensure that the dynamic instrumentation service is fully enabled when `Dynamic Instrumentation.enable()` is called.

- dynamic instrumentation: fixed a bug that might have caused probe status to fail to update correctly.

- django: This fix resolves an issue where 'span.resource' would not include the endpoint when a Handler was interrupted, such as in the case of gunicorn worker timeouts.

- CI Visibility: fixes an issue where the Intelligent Test Runner would not work when in EVP proxy mode due to missing `X-Datadog-NeedsAppKey` header.

- CI Visibility: revert to using DD_CIVISIBILITY_ITR_ENABLED (instead of \_DISABLED) to conform with other tracers.

- profiling: fixed a bug that prevented profiles from being correctly correlated to traces in gevent-based applications, thus causing code hotspot and end point data to be missing from the UI.

- docs: Fix undefined variable reference in otel documentation

- CI Visibility: fixes that Python 2.7 test results were not visible in UI due to improperly msgpack-ed data

- ASM: This fix resolves an issue where <span class="title-ref">track_user_signup_event</span> and <span class="title-ref">track_custom_event</span> where not correctly tagging the span. This could lead to the loss of some events in the sampling.

- appsec: Fixes an issue where ddtrace.appsec is imported and assumed to be available in all deployments of ddtrace

- lib-inject: This fix resolves an issue where `libdl.so.2: cannot open shared object file: No such file or directory` errors occurred when the
  injection image started.

- lib-injection: Resolves permissions errors raised when ddtrace packages are copied from the InitContainer to the shared volume.

- mariadb: This fix resolves an issue where MariaDB connection information objects not including the user or port caused exceptions to be raised.

- appsec: This fix resolves an issue in which the library attempted to finalize twice a context object used by the Application Security Management product.

- propagation: Prevent propagating unsupported non-ascii `origin` header values.

- pymongo: This upgrades the PyMongo integration to work with PyMongo versions 4.5.0 and above by choosing the root function of the integration on the basis of the PyMongo version.

- tracing: This fix resolves an issue where the <span class="title-ref">\_dd.p.dm</span> and <span class="title-ref">\_dd.\*\_psr</span> tags were applied to spans in ways that did not match their intended semantics, increasing the potential for metrics-counting bugs.

- ASM: This fix resolves issue where user information was only set in root span. Now span for user information can be selected.

- sqlalchemy: sqlalchemy rollbacks could previously cause intermittent deadlocks in some cases. To fix this `DD_TRACE_SPAN_AGGREGATOR_RLOCK` was introduced in 1.16.2 with the default as `False`. We are now changing the default to `True`.

### Other Changes

- Adds a <span class="title-ref">get_version</span> method to each integration and updates the basic template for developing an integration to include this method. The <span class="title-ref">get_version</span> method returns the integration's package distribution version and is to be included in the APM Telemetry integrations payload.
- Add a <span class="title-ref">ddtrace_iast_flask_patch</span> function defined in <span class="title-ref">ddtrace.appsec.iast</span> to ensure that the main Flask <span class="title-ref">app.py</span> file is patched for IAST propagation. This function should be called before the <span class="title-ref">app.run()</span> call. You only need this if you have set <span class="title-ref">DD_IAST_ENABLED=1</span>. Only the main file needs to call this functions, other imported modules are automatically patched.
- docs: Fixes formatting in ddtrace docs.
- ASM: Improve default value of regex for query string obfuscation. Rename env var `DD_TRACE_OBFUSCATION_QUERY_STRING_PATTERN` to `DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP`.

---

## v1.18.0

### Prelude

Data Streams Monitoring (DSM) has added support for AWS Kinesis

**Breaking change** for CI Visibility: `test.suite` and `test.full_name` are changed, so any visualization or monitor that uses these fields is potentially affected.

### Deprecation Notes

- `DD_CALL_BASIC_CONFIG` will be removed in the upcoming 2.0.0 release. As an alternative to `DD_CALL_BASIC_CONFIG`, you can call `logging.basicConfig()` to configure logging in your application.
- `DD_LOG_FORMAT` is deprecated and will be removed in 2.0.0. As an alternative, please follow the log injection formatting as provided in the [log injection docs](https://ddtrace.readthedocs.io/en/stable/advanced_usage.html#update-log-format).

### New Features

- CI Visibility: added tracing support for pytest-benchmark

- ASM: The vulnerability report now includes a feature to scrub potentially sensitive information. This scrubbing process looks for common patterns, and it can be further expanded using environment variables such as `DD_IAST_REDACTION_NAME_PATTERN` and `DD_IAST_REDACTION_VALUE_PATTERN`. See the [docs](https://ddtrace.readthedocs.io/en/stable/configuration.html#DD_IAST_REDACTION_ENABLED) for more information.

- DSM: Adds DSM support for AWS Kinesis. For information about DSM, see the [official documentation](https://docs.datadoghq.com/data_streams/). This change requires users to use botocore version 1.26.30 or later and update calls to Kinesis' <span class="title-ref">PutRecord</span>, <span class="title-ref">PutRecords</span>, and <span class="title-ref">GetRecords</span> calls with the StreamARN argument.

- pytest: This change introduces an option to the pytest plugin to disable ddtrace: `--no-ddtrace`

- CI visibility: Adds support for tracking repository URLs via the BITBUCKET_GIT_HTTP_ORIGIN environment variable

- CI visibility: Adds CodeFresh integration

- CI Visibility: Beta release of `pytest` support for the [Intelligent Test Runner](https://docs.datadoghq.com/continuous_integration/intelligent_test_runner/) .

- openai: `tiktoken` has been introduced as an optional package dependency to calculate the number of
  tokens used in a prompt for a streamed completion or streamed chat completion. To enable this feature, install `ddtrace[openai]` or `tiktoken`. If `tiktoken` is not installed, the prompt token count will be continue to be estimated instead.

- Allows the use of a new backend for storing and exporting profiling data. This feature can be enabled for now by setting the DD_PROFILING_EXPORT_LIBDD_ENABLED environment variable to true. This should improve performance while decreasing memory overhead.

### Known Issues

- sqlalchemy: sqlalchemy rollbacks can intermittently cause deadlocks in some cases. If experiencing this issue, set `DD_TRACE_SPAN_AGGREGATOR_RLOCK=True`. After testing and feedback we intend to make True the default value.

### Bug Fixes

- CI Visibility: fixes an issue where the CIVisibility client would raise an exception if it was started in agentless mode without the DD_API_KEY set

- core: This fix moves `cmake` from `install_requires` to `setup_requires`.

- data_streams: This change fixes a bug in the Kafka & SQS integrations in which the Data Streams product code incorrect set timestamps for statistics. This led to all points being submitted for the same timestamp (the start of the application).

- dynamic instrumentation: handle null literal in conditions and expressions.

- dynamic instrumentation: fixed a bug that prevented span decoration probes from being received and instrumented.

- dynamic instrumentation: ensure that probes that fail to be instrumented because of invalid conditions/expressions are reported with status `ERROR` in the UI.

- CI Visibility: This fix solves an issue where the git unshallow command wasn't called

- tracing: Ensures health metrics are tagged with the correct values.

- CI Visibility: This fix resolves an issue where test skipping was not working properly.

- langchain: This fix resolves an issue where chat messages and embedding arguments
  passed in as keyword arguments were not parsed correctly and resulted in an `ArgumentError`.

- langchain: This fix resolves an issue where `langchain.embeddings.HuggingFaceEmbeddings` embedding
  methods, and `langchain.vectorstores.Milvus.similarity_search` were patched twice due to a nested class hierarchy in `langchain`.

- profiling: prevent deadlocks while recording events of different type.

- pytest: This fix resolves an issue where test modules could be non-existent, causing errors in the CI Visibility product.

- kafka: Resolves `UnicodeDecodeError` raised when kafka messages key contain characters that are not supported by UTF-8 encoding.

- lib-injection: Adds support for non-root run applications in containers.

- This fix resolves an issue causing span tags used by the Datadog backend not to be inherited by spans that exist in a different process from their parents.

### Other Changes

- tracing: Previously the maximum size of a span tag was set to the full size of trace writer buffer (via DD_TRACE_WRITER_BUFFER_SIZE_BYTES). With this change the maximum size of span tags will be set to 10% of the size of the writer's buffer. This should decrease the frequency of encoding errors due to large span tags.

---

## v1.17.0

### Prelude

Datadog has added support for automatically creating login success or failure events when a configured Django authentication backend is used. This will automatically fill the following tags in these cases:

> - <span class="title-ref">appsec.events.users.login.success.track</span>
> - <span class="title-ref">appsec.events.users.login.failure.track</span>
> - <span class="title-ref">appsec.events.users.login.success.\[email\|login\|username\]</span>
> - <span class="title-ref">appsec.events.users.login.failure.usr.exists</span>

### New Features

- ASM: Add support for automatic user login events in Django.

- langchain: Adds integration with support for metrics, logs, and traces from LangChain requests.
  See the `docs<langchain>` for more information.

- redis: Add support for Async RedisCluster.

### Bug Fixes

- core: This fix removes the inclusion of our `benchmarks/` directory in the `ddtrace` wheels.
- internal: call `_fixupChildren` when retrieving `DDLogger`
- profiling: Fixed a regression whereby the profile exporter would not handle known request errors and asks the user to report an issue instead.
- profiling: Handles a race condition, which would occasionally throw an error, which would read `"RuntimeError: the memalloc module was not started."`
- CI visibility: fix version and step arguments gathering to enable plugin compatibility with pytest-bdd 6.1.x
- Fixed a bug that caused applications using gevent and cassandra to fail to start with the ddtrace-run command.
- tracing: This fix resolves a `google.protobuf` import error when module unloading.
- wsgi: This fix resolves an issues when trying to parse the `environ` property `HTTPS` as an HTTP header.
- Pin `cython<3` due to an incompatibility with `cython==3.0.0` and typing annotations in profiling code.
- telemetry: resolves issue with sending unnecessary duplicate logs

---

## v1.16.0

### Prelude

Application Security Management (ASM) has added support for tracing subprocess executions.

Exception Debugging allows capturing debug information from exceptions attached to traces. The information about local variables and function arguments is displayed in the Error Tracking UI and augments the traceback data already collected.

### New Features

- ASM: vulnerabilities related to insecure request cookies will be reported when `DD_APPSEC_ENABLED` is set to `true`.

- ASM: add support for tracing subprocess executions (like <span class="title-ref">os.system</span>, <span class="title-ref">os.spawn</span>, <span class="title-ref">subprocess.Popen</span> and others) and adding
  information to a span names <span class="title-ref">command_execution</span> with the new type <span class="title-ref">system</span>. Currently we add the <span class="title-ref">cmd.exec</span> or <span class="title-ref">cmd.shell</span> tags to store the full command line (<span class="title-ref">cmd.shell</span> will be used when the command is run under a shell like with <span class="title-ref">os.system</span> or <span class="title-ref">Popen</span> with <span class="title-ref">shell=True</span>), <span class="title-ref">cmd.exit_code</span> to hold the return code when available, <span class="title-ref">component</span> which will hold the Python module used and the span <span class="title-ref">resource</span> will hold the binary used. This feature requires ASM to be activated using the <span class="title-ref">DD_APPSEC_ENABLED=True</span> configuration environment variable.

- botocore: Introduces environment variable `DD_BOTOCORE_INSTRUMENT_INTERNALS` that opts into tracing certain internal functionality.

- botocore: Added message attributes to Amazon Simple Queue Service spans to support data streams monitoring.

- exception debugging: Introduced the Exception Debugging feature that allows capturing debug information from exceptions attached to traces. This new feature can be enabled via the <span class="title-ref">DD_EXCEPTION_DEBUGGING_ENABLED</span>\` environment variable.

- openai: Adds support for metrics, logs, and traces for the models, edits, images, audio, files, fine-tunes, and
  moderations endpoints. See [the docs](https://ddtrace.readthedocs.io/en/stable/integrations.html#openai) for more information.

- CI Visibility: Updates how pytest modules and test suites are reported. Modules names are now set to the fully qualified name, whereas test suites will be set to the file name.
  Before this change: {"module": "tests", "suite":"my_module/tests/test_suite.py"} After this change: {"module": "my_module.tests", "suite": "test_suite.py"}

- core: Apply `DD_TAGS` to runtime metrics.

- kafka: Adds <span class="title-ref">messaging.kafka.bootstrap.servers</span> tag for the confluent-kafka producer configuration value found in <span class="title-ref">metadata.broker.list</span> or <span class="title-ref">bootstrap.servers</span>

- tracing: This reports the GRPC package name (optional) and service name in a single <span class="title-ref">rpc.service</span> tag

### Bug Fixes

- botocore: This fix resolves an issue where ddtrace attempted to parse as URLs SQS QueueUrl attributes that were not well-formed URLs.
- psycopg: Resolves `TypeError` raised when an async cursor object is traced. This fix ensures <span class="title-ref">exc_type</span>, <span class="title-ref">exc_val</span>, and <span class="title-ref">exc_tb</span> are passed down to the wrapped object on <span class="title-ref">\_\_aexit\_\_</span>.
- Fixed an issue that prevented the library from working as expected when a combination of gevent and asyncio-based frameworks that rely on the functionalities of the ssl module is used.
- openai: Fixes the issue with `ImportError` of `TypedDict` from `typing` module in Python 3.7.
- openai: This fix resolves an issue where embeddings inputs were always tagged regardless of the configured prompt-completion sample rate.
- pytest: This fix resolves an issue where failures and non-skipped tests were not propagated properly when `unittest.TestCase` classes were used.
- Fixes an issue where harvesting runtime metrics on certain managed environments, such as Google Cloud Run, would cause ddtrace to throw an exception.
- graphql: `graphql.execute` spans are now marked as measured.
- tracing: This fix resolves an issue where negative trace ID values were allowed to propagate via Datadog distributed tracing HTTP headers.
- openai: Resolves some inconsistencies in logs generated by the image and audio endpoints, including filenames, prompts, and not logging raw binary image data.
- pymemcache: This fix resolves an issue where overriding span attributes on `HashClient` failed when `use_pooling` was set.
- This fix resolves an issue causing MyPy linting to fail on files that import ddtrace.
- The 1.15.0 version has a bug that arises when Remote Config receives both kinds of actions (removing target file configurations and loading new target file configurations) simultaneously, as the load action overrides the remove action. This error occurs if someone creates and removes Dynamic Instrumentation Probes rapidly, within a time interval shorter than the Remote Config interval (5s). To fix this issue, this update appends all new configurations and configurations to remove, and dispatches them at the end of the RC request.

---

## v1.15.0

### New Features

- pyramid: Adds http.route tag to `pyramid.request` spans.
- data_streams: Add data streams core integration and instrument the confluent Kafka library with it. For more information, check out the docs, <https://docs.datadoghq.com/data_streams/>
- dynamic instrumentation: Added support for span decoration probes.

### Bug Fixes

- ASM: This fix resolves an issue where the WAF rule file specified by DD_APPSEC_RULES was wrongly updated and modified by remote config.
- celery: Resolves an issue where hostname tags were not set in spans generated by `celery>4.0`.
- django: Resolves an issue where the resource name of django.request span did not contain the full name of a view when `DD_DJANGO_USE_HANDLER_RESOURCE_FORMAT=True`. This issue impacts `django>=4.0`.
- CI Visibility: This fix resolves the compatibility for Gitlab 16.0 deprecated urls
- openai: Resolves an issue where using an array of tokens or an array of token arrays for the Embeddings endpoint caused an AttributeError.
- profiling: Fixed an issue with gunicorn and gevent workers that occasionally caused an `AttributeError` exception to be raised on profiler start-up.
- psycopg: Fixes `ValueError` raised when dsn connection strings are parsed. This was fixed in ddtrace v1.9.0 and was re-introduced in v1.13.0.
- gunicorn: This fix ensures ddtrace threads do not block the master process from spawning workers when `DD_TRACE_DEBUG=true`. This issue impacts gunicorn applications using gevent and `python<=3.6`.

---

## v1.14.0

### Prelude

profiling: Code provenance is a feature that enhances the "My code" experience in the Datadog UI by allowing the tracer to report packaging metadata about installed source files. This information is used to distinguish between user and third-party code.

### New Features

- aws: Adds span tags for consistency with tags collected by Datadog for AWS metrics and logs.

- botocore: Adds the ability to control which botocore submodules will be patched.

- ASM: Send WAF metrics over telemetry

- pytest: This introduces test suite and module level visibility for the pytest integration. Pytest test traces will now include test session, test module, test suite, and test spans, which correlate to pytest session, pytest package, pytest module, and pytest test functions respectively.

- redis: Introducing redis command span tag max length configuration for `aioredis<aioredis>`, `aredis<aredis>`, `redis<redis>`, `rediscluster<rediscluster>`, and `yaaredis<yaaredis>` integrations.

- profiling: Code provenance is enabled by default.

- OpenAI: Add integration with support for metrics, logs and traces from
  OpenAI requests. See [the docs](https://ddtrace.readthedocs.io/en/stable/integrations.html#openai) for more information.

### Bug Fixes

- dependencies: Resolves an issue where ddtrace installs an incompatible version of cattrs when Python 3.6 is used.

- tracing: Resolves an issue where `DD_TRACE_<INTEGRATION>_ENABLED=False` could not be used to disable the following integrations when `ddtrace-run` was used: flask, django, bottle, falcon, and pyramid.

- asgi: Ensures `error.message` and `error.stack` tags are set when an exception is raised in a route.

- appsec: Fixes an encoding error when we are unable to cleanup the AppSec request context associated with a span.

- ASM: Fixes encoding error when using AppSec and a trace is partial flushed.

- CI Visibility: This fix resolves an issue where the tracer was doing extra requests if the `DD_CIVISIBILITY_ITR_ENABLED` env var was not set.

- CI Visibility: This fix resolves an issue where the API call would fail because it is reporting a null service name

- bootstrap: fixed an issue with the behavior of `ddtrace.auto` that could have caused incompatibilities with frameworks such as `gevent` when used as a programmatic alternative to the `ddtrace-run` command.

- django: Fixed a bug that prevented a Django application from starting with celery and gevent workers if `DJANGO_SETTINGS_MODULE` was not explicitly set.

- tracing: Fixes a cryptic encoding exception message when a span tag is not a string.

- ASM: fix extract_body for Django such that users of Django Rest Framework can still use custom parsers.

- flask: Remove patching for Flask hooks `app.before_first_request` and `bp.before_app_first_request` if Flask version \>= 2.3.0.

- gevent: Fix a bug that caused traceback objects to fail to pickle when using gevent.

- OpenAI: Resolved an issue where OpenAI API keys set in individual requests rather than as an environment variable caused an error in the integration.

- profiler: Fixed a bug that caused segmentation faults in applications that use protobuf as a runtime dependency.

- redis: Resolves an issue where the aioredis/aredis/yaaredis integrations cross-imported a helper method from the redis integration, which triggered redis patching before the redis integration was fully loaded.

- wsgi: Resolves an issue where accessing the `__len__` attribute on traced wsgi middlewares raised a TypeError

- django: Adds catch to guard against a ValueError, AttributeError, or NotImplementedError from being thrown when evaluating a django cache result for `db.row_count` tag.

- lib-injection: Ensure local package is installed. Previously the package
  could still be pulled from the internet causing application slowdowns.

- kafka: Fixes `TypeError` raised when arbitrary keyword arguments are passed to `confluent_kafka.Consumer`

- profiler: Fix support for latest versions of protobuf.

- psycopg: Resolves an issue where an AttributeError is raised when `psycopg.AsyncConnection` is traced.

- sanic: Resolves `sanic_routing.exceptions.InvalidUsage` error raised when gevent is installed or `DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE` is set to True.

- elasticsearch: This fix resolves an issue where the tracer would throw an error when patching unsupported versions of elasticsearch (\> 8.0). Patching is now skipped if an unsupported version is detected.

### Other Changes

- span: Increases the traceback limit in `error.stack` tags from 20 to 30
- aws_lambda: Logs warnings and exceptions on cold start only.

---

## v1.13.0

### New Features

- psycopg: This release adds support for the new psycopg3 package. This new integration has all the same tracing functionality as the previous psycopg2-binary package, with added support for new methods including async connection and async cursor classes. The release also adds support for using Django\>=4.2 with psycopg3 integrated tracing.

### Bug Fixes

- algoliasearch: This fix resolves an issue where non-text search query arguments caused Type Errors when being added as tags.

- ASM: fix calling <span class="title-ref">set_user</span> without a created span raising a <span class="title-ref">ValueError</span>.

- django: Adds fix for bug where Django cache return object throws an error if it does not implement `__bool__()`.

- kafka: Previously instantiating a subclass of kafka's Producer/Consumer classes would result in attribute errors due to patching the Producer/Consumer classes with an ObjectProxy. This fix resolves this issue by making the traced classes directly inherit from kafka's base Producer/Consumer classes.

- profiling: Fixed a regression in the memory collector that caused it to fail to cleanly re-initialize after a fork, causing error messages to be logged.

- logging: Ensure that the logging module can report thread information, such as thread names, correctly when a framework like gevent is used that requires modules cleanup.

- ASM: This fix resolves an issue where path parameters for the Flask framework were handled at response time instead of at request time for suspicious request blocking. This close a known issue opened in 1.10.0.

- lib-injection: Switch installation to install from included wheels. Prior,
  the wheels were merged together which caused conflicts between versions of dependencies based on Python version.

- tracer: Handle exceptions besides `ImportError` when integrations are loaded.

### Other Changes

- ASM: Add information about Application Security config values on <span class="title-ref">ddtrace-run --info</span>.
- otel: Fixes code formatting in api docs

---

## v1.12.0

### New Features

- tracing: Adds support for 128 bit trace ids for b3 and w3c distributing tracing headers.
- pytest: Adds the `DD_CIVISIBILITY_AGENTLESS_ENABLED` environment variable to configure the `CIVisibility` service to use an agent-less test reporting `CIVisibilityWriter`. Note that the `CIVisibility` service will use regular agent reporting by default.
- sci: Extracts and sends git metadata from environment variables `DD_GIT_REPOSITORY_URL`, `DD_GIT_COMMIT_SHA`, or from the python package specified in the `DD_MAIN_PACKAGE`. This feature can be disabled by setting `DD_TRACE_GIT_METADATA_ENABLED=False`.
- otel: Adds support for the [OpenTelemetry Tracing API](https://opentelemetry.io/docs/reference/specification/trace/api/). Please refer to the `docs <ddtrace.opentelemetry>` for more details.

### Bug Fixes

- tracing: Ensure datadog headers propagate 128 bit trace ids when `DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED=False`
- aws_lambda: Fix AttributeError raised when `ddtrace.patch_all()`, or `ddtrace.patch(aws_lambda=True)`, is set on user handler.
- aws_lambda: Fix AttributeError raised when extracting context from arguments.
- aws_lambda: Fix AttributeError raised when callable handlers are traced.
- dynamic instrumentation: Fixed an issue with expressions in metric probes that prevented them from being evaluated.
- Prevent exceptions when autoreloading modules that directly or indirectly import ddtrace with the iPython autoreload extension.
- profiling: Corrects accounting of wall and CPU time for gevent tasks within the main Python thread.
- profiling: Fixed an issue with the memory collector where a segmentation fault could occur during shutdown.
- lib-injection: The ddtrace package is now provided via the Docker image rather than relying on a run-time `pip install`. This solves issues like containers blocking network requests, installation overhead during application startup, permissions issues with the install.

---

## v1.11.0

### Deprecation Notes

- ASM: Several deprecated ASM constants that were added to the public API will be removed. This should not affect existing code as they were meant for private use only.

### New Features

- tracing: Adds support for 128 bit trace ids. To generate and propagate 128 bit trace ids using Datadog distributed tracing headers set the following configuration: `DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED=True`. Support for B3 and W3C distributed tracing headers will be added in a future change.
- aiohttp: Add missing component meta tag to aiohttp server spans.
- redis: Adds tracing support for <span class="title-ref">redis.cluster.RedisCluster</span>.
- celery: Adds automatic tracing of the `celery.beat` scheduling service to the `celery` integration.
- kafka: Adds instrumentation support for `confluent-kafka>=1.7`. See the `confluent-kafka<https://ddtrace.readthedocs.io/en/stable/integrations.html#kafka>` documentation for more information.
- dynamic instrumentation: introduced support for dynamic span probes.
- Adds source code integration with setuptools build metadata. This enables traces and profiles to be automatically tagged with git metadata to track deployments in Datadog.

### Bug Fixes

- tracing: This fix resolves an issue where making a sampling decision before the `env` span tag had been set caused sample rate data from the Datadog Agent to be ignored.
- ASM: make `track_custom_event()` also set `appsec.events.<custom_event>.track` which was missing.
- django: Fixes an issue where `http.route` was only set if `use_handler_resource_format` and `use_legacy_resource_format` were set to `False`.
- tracing: This fix resolves an issue where a very long string as a span attribute would cause that span not to be delivered. It replaces string span attributes larger than DD_TRACE_WRITER_BUFFER_SIZE_BYTES (which as of this version defaults to 8388608) with a small string containing debug information and not containing any of the original attribute string.
- ASM: Resolves installation issues with compiling native code on Windows and unknown platforms.
- aws_lambda: Fixes a `RecursionError` which is raised when aws lambda signal handlers are wrapped infinitely. This caused lambdas to crash on startup.
- botocore: Fix TypeError raised by injecting trace context into Kinesis messages.
- dynamic instrumentation: Fix a bug where the dynamic instrumentation would stop injecting function probes after the first failed one.
- dynamic instrumentation: This change fixes a bug whereby probes that have been disabled/removed from the front-end would not be removed by the client library.
- futures: Resolves an issue that prevents tasks from being submitted to a thread pool executor when gevent is used (e.g. as a worker class for gunicorn or celery).
- propagation: This fix resolves an issue where previously W3C tracestate propagation could not handle whitespace. With this fix whitespace is now removed for incoming and outgoing requests.
- httplib: Fixes an issue with patching of http client upon import
- Ensure DD_REMOTE_CONFIGURATION_ENABLED environment variable disables remote config if set to False

### Other Changes

- aws_lambda: Updates how <span class="title-ref">DD_APM_FLUSH_DEADLINE_MILLISECONDS</span> is used. Previously, we would set the deadline as the environment variable value, if set. Now, when the remaining time in an AWS Lambda invocation is less than <span class="title-ref">DD_APM_FLUSH_DEADLINE_MILLISECONDS</span>, the tracer will attempt to submit the current active spans and all finished spans. the value in the environment variable is used to subtract from the deadline. The default is still 100ms.

---

## v1.9.1

### Deprecation Notes

- gevent: `DD_GEVENT_PATCH_ALL` is deprecated and will be removed in the next major version. Gevent compatibility is now automatic and does not require extra configuration when running with `ddtrace-run`. If not using `ddtrace-run`, please import `ddtrace.auto` before calling `gevent.monkey.patch_all()`.

### Bug Fixes

- aws_lambda: Resolves an exception not being handled, which occurs when no root span is found before a lambda times out.
- gevent: This fix resolves an incompatibility between ddtrace and gevent that caused threads to hang in certain configurations, for example the profiler running in a gunicorn application's gevent worker process.

### Other Changes

- ASM: The list of headers for retrieving the IP when Application Security Management is enabled or the
  <span class="title-ref">DD_TRACE_CLIENT_IP_ENABLED</span> environment variable is set has been updated. "Via" has been removed as it rarely contains IP data and some common vendor headers have been added. You can also set the environment variable <span class="title-ref">DD_TRACE_CLIENT_IP_HEADER</span> to always retrieve the IP from the header specified as the value.

---

## v1.10.0

### Prelude

Application Security Management (ASM) has added Django support for blocking malicious users using one click within Datadog.

<div class="note">

<div class="title">

Note

</div>

One click blocking for ASM is currently in beta.

</div>

### Deprecation Notes

- dbapi: `ddtrace.ext.mongo.ROWS` is deprecated. Use `ddtrace.ext.db.ROWCOUNT` instead.

### New Features

- starlette: Add http.route tag to `starlette.request` spans.
- fastapi: Add http.route tag to `fastapi.request` spans.
- ASM: Add support for one click blocking of user ids with the Django framework using Remote Configuration Management.
- ASM: This introduces the "suspicious request blocking" feature for Django and Flask.

### Known Issues

- ASM: There is a known issue with the flask support for any rule blocking on `server.request.path_params`. The request will be correctly blocked but the client application will be receiving and processing the suspicious request. Possible workaround: use `server.request.uri.raw` instead, if you want the request to be blocked before entering the flask application.

### Bug Fixes

- dbapi: The dbapi integration no longer assumes that a cursor object will have a rowcount as not all database drivers implement rowcount.

- dbm: Support sql queries with the type `byte`.

- elasticsearch: Omit large `elasticsearch.body` tag values that are
  greater than 25000 characters to prevent traces from being too large to send.

- aws_lambda: This fix resolves an issue where existing signals were wrapped multiple times.

- profiling: Handles a race condition on process shutdown that would cause an error about a module not being started to occasionally appear in the logs.

- Fix for KeyError exceptions when when <span class="title-ref">ASM_FEATURES</span> (1-click activation) disabled all ASM products. This could cause 1-click activation to work incorrectly in some cases.
- ASM: Solve some corner cases where a Flask blocking request would fail because headers would be already sent.
- ASM: Solve the content-type not always being correct in blocking responses.
- ASM: Ensure the blocking responses have the following tags: <span class="title-ref">http.url</span>, <span class="title-ref">http.query_string</span>, <span class="title-ref">http.useragent</span>, <span class="title-ref">http.method</span>, <span class="title-ref">http.response.headers.content-type</span> and <span class="title-ref">http.response.headers.content-length</span>.
- ASM: fix memory leaks and memory corruption in the interface between ASM and the WAF library
- psycopg2: Fixes a bug with DSN parsing integration.

### Other Changes

- remote_config: Change the level of remote config startup logs to debug.

---

## v1.9.0

### Prelude

Application Security Management (ASM) has added Django support for blocking malicious IPs using one click within Datadog.

<div class="note">

<div class="title">

Note

</div>

One click blocking for ASM is currently in beta.

</div>

Application Security Management (ASM) has added Flask support for blocking malicious IPs using one click within Datadog.

<div class="note">

<div class="title">

Note

</div>

One click blocking for ASM is currently in beta.

</div>

### Deprecation Notes

- grpc: Deprecates `ddtrace.contrib.grpc.constants.GRPC_PORT_KEY`. Use `ddtrace.ext.net.TARGET_PORT` instead.
- dbapi: `ddtrace.ext.sql.ROWS` is deprecated. Use `ddtrace.ext.db.ROWCOUNT` instead.
- cassandra: `ddtrace.ext.cassandra.ROW_COUNT` is deprecated. Use `ddtrace.ext.db.ROWCOUNT` instead.

### New Features

- Enable traces to be sent before an impending timeout for `datadog_lambda>=4.66.0`. Use `DD_APM_FLUSH_DEADLINE` to override the default flush deadline. The default is the AWS Lambda function configured timeout limit.

- debugger: Add dynamic log probes to that generate a log message and optionally capture local variables, return value and exceptions

- tracing: Add support for enabling collecting of HTTP request client IP addresses as the `http.client_ip` span tag. You can set the `DD_TRACE_CLIENT_IP_ENABLED` environment variable to `true` to enable. This feature is disabled by default.

- ASM: add support for one click blocking of IPs with the Django framework using Remote Configuration Management.

- ASM: add support for one click blocking of IPs with the Flask framework using
  Remote Configuration Management.

- ASM: also fetch loopback IPs if client IP fetching is enabled (either via ASM or DD_TRACE_CLIENT_IP_ENABLED).

- ASM: Enable ability to remotely activate and configure ASM features. To enable, check the Python Security page in your account. Note that this is a beta feature.

- profiling: Collects endpoint invocation counts.

- dynamic instrumentation: Python 3.11 is now supported.

- graphene: Adds support for Python 3.11.

- graphql: Adds support for Python 3.11.

- httpx: Add support for `httpx<0.14.0,>=0.9.0`.

- tracer/span: Add `Span.finish_with_ancestors` method to enable the abrupt
  finishing of a trace in cases where the trace or application must be immediately terminated.

### Known Issues

- remote config: There is a known issue with remote configuration management (RCM) when paired with gevent which can cause child processes to deadlock. If you are experiencing issues, we recommend disabling RCM with `DD_REMOTE_CONFIGURATION_ENABLED=false`. Note, this will disable one click activation for ASM.
- gunicorn: ddtrace-run does not work with gunicorn. To instrument a gunicorn application, follow the instructions [here](https://ddtrace.readthedocs.io/en/latest/integrations.html#gunicorn).

### Bug Fixes

- fastapi: Previously, custom fastapi middlewares configured after application startup were not traced. This fix ensures that all fastapi middlewares are captured in the <span class="title-ref">fastapi.request</span> span.

- tracing: Pads trace_id and span_ids in b3 headers to have a minimum length of 16.

- Fix full stacktrace being sent to the log on remote config connection errors.

- httpx: Only patch `httpx.AsyncClient` for `httpx>=0.11.0`.

- tracing: This fix resolves an issue with the encoding of traces when using the v0.5 API version with the Python optimization option flag `-O` or the `PYTHONOPTIMIZE` environment variable.

- pylons: This fix resolves an issue where `str.decode` could cause critical unicode decode errors when ASM is enabled. ASM is disabled by default.

- gevent: This fix resolves incompatibility under 3.8\>=Python\<=3.10 between `ddtrace-run` and applications that depend on `gevent`, for example `gunicorn` servers. It accomplishes this by keeping copies that have not been monkey patched by `gevent` of most modules used by `ddtrace`. This "module cloning" logic can be controlled by the environment variable `DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE`. Valid values for this variable are "1", "0", and "auto". "1" tells `ddtrace` to run its module cloning logic unconditionally, "0" tells it never to run that logic, and "auto" tells it to run module cloning logic *only if* `gevent` is accessible from the application's runtime. The default value is "0".

- lib-injection: Use package versions published to PyPI to install the
  library. Formerly the published image was installing the package from source using the tagged commit SHA which resulted in slow and potentially failing installs.

- profiler: Handles potential `AttributeErrors` which would arise while collecting frames during stack unwinding in Python 3.11.

- remote config: ensure proper validation of responses from the agent.

---

## v1.8.0

### Upgrade Notes

- ASM: libddwaf upgraded to version 1.6.1 using a new library loading mechanism
- profiling: upgrades the profiler to support the `v2.4` backend API for profile uploads, using a new request format.

### Deprecation Notes

- `DD_REMOTECONFIG_POLL_SECONDS` environment variable is deprecated and will be removed in v2.0. Please use `DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS` instead.

### New Features

- CI Visibility: Add support for CI provider buddy.works

- The component tag has been added for all auto-instrumented spans. The value of the component tag is equal to the name of the integration that produced the span.

- tracing: Adds support for IPv6 agent hostnames for <span class="title-ref">DD_AGENT_HOST</span>.

- elasticsearch: Update `elasticsearch` integration to add support for `opensearch-py`. See [the elasticsearch documentation](https://ddtrace.readthedocs.io/en/stable/integrations.html#elasticsearch) for more information.

- ASM: one click activation enabled by default using Remote Configuration Management (RCM). Set `DD_REMOTE_CONFIGURATION_ENABLED=false` to disable this feature.

- ASM: New Application Security Events Tracking API, starting with the functions `track_user_login_success_event` and
  `track_user_login_failure_event` for tracking user logins (it will also internally call `set_user`) and `track_custom_event` for any custom events. You can find these functions in the `ddtrace.appsec.trace_utils` module. Calling these functions will create new tags under the `appsec.events` namespace (`appsec.events.user.login` for logins) allowing you to track these events with Datadog. In the future this will be used to provide protection against account takeover attacks (ATO). Public documentation will be online soon.

- celery: Enhances context tags containing dictionaries so that their contents are sent as individual tags (issue \#4771).

- tornado: Support custom error codes: <https://ddtrace.readthedocs.io/en/stable/advanced_usage.html#custom-error-codes>.

- CI Visibility: Support reliably linking tests to the pipeline that executed them.

### Known Issues

- profiling: There is currently a known performance regression issue with the profiler's code provenance feature. Note that this feature is disabled by default and will only be enabled if `DD_PROFILING_ENABLE_CODE_PROVENANCE` is set to true.

### Bug Fixes

- This fix improves a cryptic error message encountered during some `pip install ddtrace` runs under pip versions \<18.
- dynamic instrumentation: remove unnecessary log line from application start up
- This fix removes unintended url parts in the `http.url` tag.
- botocore: Before this change, the botocore integration stripped newlines from the JSON string encoded in the data blob of Amazon Kinesis records. This change includes a terminating newline if it is present in the decoded data.
- profiling: This fix resolves an issue in Python 3.11 where a PyFrameObject strong reference count was not properly decremented in the stack collector.
- telemetry: This fix resolves an issue when we try to fetch `platform.libc_ver()` on an unsupported system.
- Fix for ValueError when `@` is not present in network location but other part of the url.

### Other Changes

- profiler: CPU overhead reduction.

---

## v1.7.0

### Prelude

Initial library support has been added for Python 3.11.

<div class="note">

<div class="title">

Note

</div>

Continuous Profiler and Dynamic Instrumentation are not yet compatible and must be disabled in order to use the library with Python 3.11. Support for them will be added in a future release. To track the status, see the [Support Python 3.11](https://github.com/DataDog/dd-trace-py/issues/4149) issue on GitHub.

</div>

### Upgrade Notes

- The default propagation style configuration changes to `DD_TRACE_PROPAGATION_STYLE=tracecontext,datadog`. To only support Datadog propagation and retain the existing default behavior, set `DD_TRACE_PROPAGATION_STYLE=datadog`.
- tracer: support for Datadog Agent v5 has been dropped. Datadog Agent v5 is no longer supported since ddtrace==1.0.0. See <https://ddtrace.readthedocs.io/en/v1.0.0/versioning.html#release-support> for the version support.
- Python 3.11: Continuous Profiler and Dynamic Instrumentation must be disabled as they do not current support Python 3.11.
- The configured styles in `DD_TRACE_PROPAGATION_STYLE_EXTRACT` are now evaluated in order to specification. To keep the previous fixed evaluation order, set: `DD_TRACE_PROPAGATION_STYLE_EXTRACT=datadog,b3,b3 single header`.
- tracing: upgrades the default trace API version to `v0.5` for non-Windows systems. The `v0.5` trace API version generates smaller payloads, thus increasing the throughput to the Datadog agent especially with larger traces.
- tracing: configuring the `v0.5` trace API version on Windows machines will raise a `RuntimeError` due to known compatibility issues. Please see <https://github.com/DataDog/dd-trace-py/issues/4829> for more details.

### Deprecation Notes

- propagation: Configuration of propagation style with `DD_TRACE_PROPAGATION_STYLE=b3` is deprecated and will be removed in version 2.0.0. Please use the newly added `DD_TRACE_PROPAGATION_STYLE=b3multi` instead.
- aws: The boto, botocore and aiobotocore integrations no longer include all API parameters by default. To retain the deprecated behavior, set the environment variable `DD_AWS_TAG_ALL_PARAMS=1`. The deprecated behavior and environment variable will be removed in v2.0.0.

### New Features

- django: add configuration option to allow a resource format like <span class="title-ref">{method} {handler}.{url_name}</span> in projects with Django \<2.2.0
- django: Adds the `DD_DJANGO_INCLUDE_USER_NAME` option to toggle whether the integration sets the `django.user.name` tag.
- Added environment variable `DD_TRACE_PROPAGATION_STYLE` to configure both injection and extraction propagation styles. The configured styles can be overridden with environment variables `DD_TRACE_PROPAGATION_STYLE_INJECT` and `DD_TRACE_PROPAGATION_STYLE_EXTRACT`.
- tracing: This introduces `none` as a supported propagator for trace context extraction and injection. When `none` is the only propagator listed, the corresponding trace context operation is disabled. If there are other propagators in the inject or extract list, the none propagator has no effect. For example `DD_TRACE_PROPAGATION_STYLE=none`
- ASM: now http.client_ip and network.client.ip will only be collected if ASM is enabled.
- tracing: Adds support for W3C Trace Context propagation style for distributed tracing. The `traceparent` and `tracestate` HTTP headers are enabled by default for all incoming and outgoing HTTP request headers. The Datadog propagation style continue to be enabled by default.
- flask: Adds support for streamed responses. Note that two additional spans: `flask.application` and `flask.response` will be generated.
- profiling: Adds support for Python 3.11.
- tracer: added support for Python 3.11.

### Bug Fixes

- ASGI: response headers are correctly processed instead of ignored
- Fix issue with `attrs` and `contextlib2` version constraints for Python 2.7.
- CGroup file parsing was fixed to correctly parse container UUID for PCF containers.
- ASM: Do not raise exceptions when failing to parse XML request body.
- ASM: fix a body read problem on some corner case where don't passing the content length makes wsgi.input.read() blocks.
- aws: We are reducing the number of API parameters that the boto, botocore and aiobotocore integrations collect as span tags by default. This change limits span tags to a narrow set of parameters for specific AWS APIs using standard tag names. To opt out of the new default behavior and collect no API parameters, set the environment variable `DD_AWS_TAG_NO_PARAMS=1`. To retain the deprecated behavior and collect all API parameters, set the environment variable `DD_AWS_TAG_ALL_PARAMS=1`.
- tracing: make `ddtrace.context.Context` serializable which fixes distributed tracing across processes.
- django: avoid `SynchronousOnlyOperation` when failing to retrieve user information.
- Remove `forbiddenfruit` as dependency and rollback `wrapt` changes where `forbiddenfruit` was called. IAST: Patch builtins only when IAST is enabled.
- httpx: Fixes an incompatibility from `httpx==0.23.1` when the `URL.raw` property is not available.
- Fix error in patching functions. `forbiddenfruit` package has conflicts with some libraries such as `asynctest`. This conflict raises `AttributeError` exception. See issue \#4484.
- tracer: This fix resolves an issue where the rate limiter used for span and trace sampling rules did not reset the time since last call properly if the rate limiter already had max tokens. This fix resets the time since last call always, which leads to more accurate rate limiting.
- Ensure that worker threads that run on start-up are recreated at the right time after fork on Python \< 3.7.
- tracing: This fix resolves an issue where the `DD_SERVICE_MAPPING` mapped service names were not used when updating span metadata with the `DD_VERSION` set version string.
- wsgi: This fix resolves an issue where `BaseException` raised in a WSGI application caused spans to not be submitted.
- library injection: Pin the library version in the library injection image. Prior, the latest version of `ddtrace` would always be installed, regardless of the image version.
- Fix error in the agent response payload when the user disabled ASM in a dashboard using 1-click Remote Configuration.
- flask: add support for flask v2.3. Remove deprecated usages of `flask._app_ctx_stack` and `flask._request_ctx_stack`.
- The specification of `DD_TRACE_PROPAGATION_STYLE_EXTRACT` now respects the configured styles evaluation order. The evaluation order had previously been fixed and so the configured order was ignored.
- tracing: Ensures that encoding errors due to wrong span tag types will be logged. Previously, if non-text span tags were set, this resulted in v0.5 encoding errors to be output to `stderr` instead of to a logger.

### Other Changes

- Kubernetes library injection: run commands as non-root user.
- tracing: The value of `ddtrace.constants.PID` has been changed from `system.pid` to `process_id`. All spans will now use the metric tag of `process_id` instead.
- tracing: The exception logged for writing errors no longer includes a long, unhelpful stack trace. The message now also includes the number of traces dropped and the number of retries attempted.

---

## v1.6.0

### Prelude

Application Security Management (ASM) has added support for preventing attacks by blocking malicious IPs using one click within Datadog.

<div class="note">

<div class="title">

Note

</div>

One click activation for ASM is currently in beta.

</div>

Dynamic instrumentation allows instrumenting a running service dynamically to extract runtime information that could be useful for, e.g., debugging purposes, or to add extra metrics without having to make code changes and re-deploy the service. See <https://ddtrace.readthedocs.io/en/stable/configuration.html> for more details.

### Upgrade Notes

- Pin \[attrs\](<https://pypi.org/project/attrs/>) dependency to version `>=20` due to incompatibility with \[cattrs\](<https://pypi.org/project/cattrs/>) version `22.1.0`.
- Use `Span.set_tag_str()` instead of `Span.set_tag()` when the tag value is a text type as a performance optimizations in manual instrumentation.

### New Features

- ASM: add support for one click activation using Remote Configuration Management (RCM). Set `DD_REMOTE_CONFIGURATION_ENABLED=true` to enable this feature.
- ASM: ip address collection will be enabled if not explicitly disabled and appsec is enabled.
- tracing: HTTP query string tagged by default in http.url tag (sensitive query strings will be obfuscated).
- Django: set <span class="title-ref">usr.id</span> tag by default if <span class="title-ref">request.user</span> is authenticated.
- Introduced the public interface for the dynamic instrumentation service. See <https://ddtrace.readthedocs.io/en/stable/configuration.html> for more details.
- Add `Span.set_tag_str()` as an alternative to the overloaded functionality of `Span.set_tag()` when the value can be coerced to unicode text.
- Enable `telemetry <Instrumentation Telemetry>` collection when tracing is enabled.

### Bug Fixes

- ASM: only report actor.ip on attack.
- aioredis: added exception handling for <span class="title-ref">CancelledError</span> in the aioredis integration.
- CI Visibility: fixed AppVeyor integration not extracting the full commit message.
- Add iterable methods on TracedCursor. Previously these were not present and would cause iterable usage of cursors in DB API integrations to fail.
- Fix parsing of the `DD_TAGS` environment variable value to include support for values with colons (e.g. URLs). Also fixed the parsing of invalid tags that begin with a space (e.g. `DD_TAGS=" key:val"` will now produce a tag with label `key`, instead of `key`, and value `val`).
- opentracing: don't raise an exception when distributed tracing headers are not present when attempting to extract.
- sqlite3: fix error when using `connection.backup` method.
- Change dependency from `` backport_ipaddress` to ``ipaddress`. Only install`ipaddress\`\` for Python \< 3.7.
- gevent: disable gevent after fork hook which could result in a performance regression.
- profiling: restart automatically on all Python versions.
- profiling: fixes an issue with Gunicorn child processes not storing profiling events.
- wsgi: when using more than one nested wsgi traced middleware in the same app ensure wsgi spans have the correct parenting.

### Other Changes

- tracing: add http.route tag to root span for Flask framework.

---

## v1.5.0

### New Features

- graphene: add support for `graphene>=2`. [See the graphql documentation](https://ddtrace.readthedocs.io/en/stable/integrations.html#graphql) for more information.
- Add support for aiobotocore 1.x and 2.x.
- ASM: add user information to traces.
- ASM: collect http client_ip.
- ASM: configure the sensitive data obfuscator.
- ASM: Detect attacks on Pylons body.
- ASM: propagate user id.
- ASM: Support In-App WAF metrics report.
- Collect user agent in normalized span tag `http.useragent`.
- ASM: Detect attacks on XML body (for Django, Pylons and Flask).
- Adds support for Lambda profiling, which can be enabled by starting the profiler outside of the handler (on cold start).
- profiler: collect and export the class name for the wall time, CPU time and lock profiles, when available.
- add DD_PYMONGO_SERVICE configuration
- ASM: Redact sensitive query strings if sent in http.url.
- redis: track the connection client_name.
- rediscluster: add service name configuration with `DD_REDISCLUSTER_SERVICE`
- snowflake: add snowflake query id tag to `sql.query` span

### Bug Fixes

- aiohttp_jinja2: use `app_key` to look up templates.
- ASM: (flask) avoid json decode error while parsing request body.
- ASM: fix Python 2 error reading WAF rules.
- ASM: reset wsgi input after reading.
- tracing: fix handling of unicode `_dd.origin` tag for Python 2
- tracing: fix nested web frameworks re-extracting and activating HTTP context propagation headers.
- requests: fix split-by-domain service name when multiple `@` signs are present in the url
- profiling: internal use of RLock needs to ensure original threading locks are used rather than gevent threading lock. Because of an indirection in the initialization of the original RLock, we end up getting an underlying gevent lock. We work around this behavior with gevent by creating a patched RLock for use internally.
- profiler: Remove lock for data structure linking threads to spans to avoid deadlocks with the trade-off of correctness of spans linked to threads by stack profiler at a given point in time.
- profiling: fix a possible deadlock due to spans being activated unexpectedly.

---

## v1.4.0

### New Features

- graphql: add tracing for `graphql-core>2`. See [the graphql documentation](https://ddtrace.readthedocs.io/en/stable/integrations.html#graphql) for more information.
- ASM: Detect attacks on Django body.
- ASM: Detect attacks on Flask request cookies
- ASM: Detect attacks on Django request cookies
- ASM: Detect attacks on Pylons HTTP query.
- ASM: Detect attacks on Pylons request cookies
- ASM: detect attacks on Pylons path parameters.
- ASM: Report HTTP method on Pylons framework
- ASM: Collect raw uri for Pylons framework.
- AppSec: collect response headers
- ASM: Detect attacks on Flask body.
- ASM: Detect attacks on path parameters
- The profiler now supports Windows.
- The profiler now supports code provenance reporting. This can be enabled by using the `enable_code_provenance=True` argument to the profiler or by setting the environment variable `DD_PROFILING_ENABLE_CODE_PROVENANCE` to `true`.

### Bug Fixes

- flask: add support for `flask>=2.2.0`
- Fixed the environment variable used for log file size bytes to be `DD_TRACE_LOG_FILE_SIZE_BYTES` as documented.
- jinja2: fix handling of template names which are not strings.
- Fixed support for pytest-bdd 6.
- Fixes cases where a pytest test parameter object string representation includes the `id()` of the object, causing the test fingerprint to constantly change across executions.
- wsgi: ignore GeneratorExit Exception in wsgi.response spans
- wsgi: ensures resource and http tags are always set on <span class="title-ref">wsgi.request</span> spans.

### Other Changes

- profiler: don't initialize the `AsyncioLockCollector` unless asyncio is
  available. This prevents noisy logs messages from being emitted in Python 2.

- docs: Added troubleshooting section for missing error details in the root span of a trace.

---

## v1.3.0

### New Features

- internal: Add support for Datadog trace tag propagation
- django: added `DD_DJANGO_INSTRUMENT_TEMPLATES=false` to allow tracing of Django template rendering.
- internal: Add sampling mechanism trace tag
- Add environment variables to write `ddtrace` logs to a file with `DD_TRACE_LOG_FILE`, `DD_TRACE_LOG_FILE_LEVEL`, and `DD_TRACE_FILE_SIZE_BYTES`
- Adds pytest-bdd integration to show more details in CI Visibility product.

### Bug Fixes

- starlette: Add back removed `aggregate_resources` feature.
- fastapi: Add back removed `aggregate_resources` feature.
- aiomysql: fix `AttributeError: __aenter__` when using cursors as context managers.
- asgi, starlette, fastapi: Exclude background tasks duration from web request spans.
- asgi: set the `http.url` tag using the hostname in the request header before defaulting to the hostname of the asgi server.
- mypy: Avoid parsing redis asyncio files when type checking Python 2
- starlette: Add back removed `ddtrace.contrib.starlette.get_resource` and `ddtrace.contrib.starlette.span_modifier`.
- fastapi: Add back removed `ddtrace.contrib.fastapi.span_modifier`.
- internal: fix exception raised for invalid values of `DD_TRACE_X_DATADOG_TAGS_MAX_LENGTH`.
- flask_caching: fix redis tagging after the v2.0 release.
- redis: create default Pin on asyncio client. Not having a Pin was resulting in no traces being produced for the async redis client.

### Other Changes

- perf: don't encode default parent_id value.
- profiling: add support for protobuf \>=4.0.

---

## v1.2.0

### Upgrade Notes

- The profiler `asyncio_loop_policy` attribute has been renamed to `asyncio_loop_policy_class` to accept a user-defined class. This guarantees the same asyncio loop policy class can be used process children.

### New Features

- Add tracing support for `aiomysql>=0.1.0`.

- Add support for `grpc.aio`.

- botocore: allow defining error status codes for specific API operations.

  See our `botocore` document for more information on how to enable this feature.

- ciapp: detect code owners of PyTest tests

- The memory profile collector can now entirely disabled with the `DD_PROFILING_MEMORY_ENABLED` environment variable.

- psycopg2: add option to enable tracing `psycopg2.connect` method.

  See our `psycopg2` documentation for more information.

- Add asyncio support of redis ≥ 4.2.0

### Bug Fixes

- Fixes deprecation warning for `asyncio.coroutine` decorator.

- internal: normalize header names in ASM

- profiling: implement `__aenter__` and `__aexit__` methods on `asyncio.Lock` wrapper.

- tracing: fix issue with `ddtrace-run` having the wrong priority order of tracer host/port/url env variable configuration.

- django,redis: fix unicode decode error when using unicode cache key on Python 2.7

- fastapi/starlette: when using sub-apps, formerly a call to `/sub-app/hello/{name}` would give a resource name of `/sub-app`. Now the full path `/sub-app/hello/{name}` is used for the resource name.

- sanic: Don't send non-500s error traces.

- pin protobuf to version `>=3,<4` due to incompatibility with version `4.21`.

- Fixes a performance issue with the profiler when used in an asyncio application.

- The profiler now copy all user-provided attributes on fork.

- pytest: Add note for disabling ddtrace plugin as workaround for side-effects

- Set required header to indicate top level span computation is done in the client to the Datadog agent. This fixes an issue where spans were erroneously being marked as top level when partial flushing or in certain asynchronous applications.

  The impact of this bug is the unintended computation of stats for non-top level spans.

### Other Changes

- The default number of events kept by the profiler has been reduced to decreased CPU and memory overhead.

---

## v1.1.4

### Bug Fixes

- pin protobuf to version `>=3,<4` due to incompatibility with version `4.21`.

---

## v1.1.3

### Bug Fixes

- tracing: fix issue with `ddtrace-run` having the wrong priority order of tracer host/port/url env variable configuration.
- sanic: Don't send non-500s error traces.
- Fixes a performance issue with the profiler when used in an asyncio application.

---

## v1.1.2

### Bug Fixes

- profiling: implement `__aenter__` and `__aexit__` methods on `asyncio.Lock` wrapper.

---

## v1.1.1

### Bug Fixes

- internal: normalize header names in ASM

- Set required header to indicate top level span computation is done in the client to the Datadog agent. This fixes an issue where spans were erroneously being marked as top level when partial flushing or in certain asynchronous applications.

  The impact of this bug is the unintended computation of stats for non-top level spans.

---

## v1.1.0

### Prelude

The Datadog APM Python team is happy to announce the release of v1.0.0 of ddtrace. This release introduces a formal `versioning policy<versioning>` that simplifies the public `interface<versioning_interfaces>` and defines a `release version policy<versioning_release>` for backwards compatible and incompatible changes to the public interface.

The v1.0.0 release is an important milestone for the library as it has grown substantially in scope. The first commit to the library was made on June 20, 2016. Nearly sixty minor releases later, the library now includes over sixty integrations for libraries. And the library has expanded from Tracing to support the Continuous Profiler and CI Visibility.

<div class="important">

<div class="title">

Important

</div>

Before upgrading to v1.0.0, we recommend users install `ddtrace>=0.60.0,<1.0.0` and enable deprecation warnings. All removals to the library interface and environment variables were deprecated on 0.x branch. Consult `Upgrade 0.x<upgrade-0.x>` for recommendations on migrating from the 0.x release branch.

</div>

<div class="note">

<div class="title">

Note

</div>

The changes to environment variables apply only to the configuration of the ddtrace library and not the Datadog Agent.

</div>

#### Upgrading summary

##### Functionality changes

The default logging configuration functionality of `ddtrace-run` has changed to address conflicts with application logging configuration. See `note on the new default behavior<disable-basic-config-call-by-default>` and `note on deprecation<deprecate-basic-config-call>` for future removal.

##### Removed legacy environment variables

These environment variables have been removed. In all cases the same functionality is provided by other environment variables and replacements are provided as recommended actions for upgrading.

| Variable                            | Replacement                        | Note                                   |
|-------------------------------------|------------------------------------|----------------------------------------|
| `DATADOG_` prefix                   | `DD_` prefix                       | `📝<remove-datadog-envs>`              |
| `DATADOG_SERVICE_NAME`              | `DD_SERVICE`                       | `📝<remove-legacy-service-name-envs>`  |
| `DD_LOGGING_RATE_LIMIT`             | `DD_TRACE_LOGGING_RATE`            | `📝<remove-logging-env>`               |
| `DD_TRACER_PARTIAL_FLUSH_ENABLED`   | `DD_TRACE_PARTIAL_FLUSH_ENABLED`   | `📝<remove-partial-flush-enabled-env>` |
| `DD_TRACER_PARTIAL_FLUSH_MIN_SPANS` | `DD_TRACE_PARTIAL_FLUSH_MIN_SPANS` | `📝<remove-partial-flush-min-envs>`    |

##### Removed legacy tracing interfaces

These methods and module attributes have been removed. Where the same functionality is provided by a different public method or module attribute, a recommended action is provided for upgrading. In a few limited cases, because the interface was no longer used or had been moved to the internal interface, it was removed and so no action is provided for upgrading.

| Module             | Method/Attribute           | Note                                  |
|--------------------|----------------------------|---------------------------------------|
| `ddtrace.context`  | `Context.clone`            | `📝<remove-clone-context>`            |
| `ddtrace.pin`      | `Pin.app`                  | `📝<remove-pin-app>`                  |
|                    | `Pin.app_type`             | `📝<remove-pin-apptype>`              |
| `ddtrace.sampler`  | `Sampler.default_sampler`  | `📝<remove-default-sampler>`          |
| `ddtrace.span`     | `Span.tracer`              | `📝<remove-span-tracer>`              |
|                    | `Span.__init__(tracer=)`   | `📝<remove-span-init-tracer>`         |
|                    | `Span.meta`                | `📝<remove-span-meta>`                |
|                    | `Span.metrics`             | `📝<remove-span-metrics>`             |
|                    | `Span.set_meta`            | `📝<remove-span-set-meta>`            |
|                    | `Span.set_metas`           | `📝<remove-span-set-metas>`           |
|                    | `Span.pprint`              | `📝<remove-span-pprint>`              |
| `ddtrace.tracer`   | `Tracer.debug_logging`     | `📝<remove-tracer-debug-logging>`     |
|                    | `Tracer.get_call_context`  | `📝<remove-tracer-get-call-context>`  |
|                    | `Tracer.tags`              | `📝<remove-tracer-tags>`              |
|                    | `Tracer.writer`            | `📝<remove-tracer-writer>`            |
|                    | `Tracer.__call__`          | `📝<remove-tracer-call>`              |
|                    | `Tracer.global_excepthook` | `📝<remove-tracer-global-excepthook>` |
|                    | `Tracer.log`               | `📝<remove-tracer-log>`               |
|                    | `Tracer.priority_sampler`  | `📝<remove-tracer-priority-sampler>`  |
|                    | `Tracer.sampler`           | `📝<remove-tracer-sampler>`           |
|                    | `Tracer.set_service_info`  | `📝<remove-tracer-set-service-info>`  |
| `ddtrace.ext`      | `SpanTypes`                | `📝<remove-span-types-enum>`          |
| `ddtrace.helpers`  | `get_correlation_ids`      | `📝<remove-helpers>`                  |
| `ddtrace.settings` | `Config.HTTPServerConfig`  | `📝<remove-config-httpserver>`        |

##### Removed legacy integration tracing

These tracing functions in integrations were no longer used for automatic instrumentation so have been removed. Any manual instrumentation code in an application will need to be replaced with `ddtrace.patch_all` or `ddtrace.patch` when upgrading.

| Module                        | Function/Class                          |                                          |
|-------------------------------|-----------------------------------------|------------------------------------------|
| `ddtrace.contrib.cassandra`   | `get_traced_cassandra`                  | `📝<remove-cassandra-traced>`            |
| `ddtrace.contrib.celery`      | `patch_task`                            | `📝<remove-celery-patch-task>`           |
| `ddtrace.contrib.celery`      | `unpatch_task`                          | `📝<remove-celery-unpatch-task>`         |
| `ddtrace.contrib.flask`       | `middleware.TraceMiddleware`            | `📝<remove-flask-middleware>`            |
| `ddtrace.contrib.mongoengine` | `trace_mongoengine`                     | `📝<remove-mongoengine-traced>`          |
| `ddtrace.contrib.mysql`       | `get_traced_mysql_connection`           | `📝<remove-mysql-legacy>`                |
| `ddtrace.contrib.psycopg`     | `connection_factory`                    | `📝<remove-psycopg-legacy>`              |
| `ddtrace.contrib.pymongo`     | `patch.trace_mongo_client`              | `📝<remove-pymongo-client>`              |
| `ddtrace.contrib.pymysql`     | `tracers.get_traced_pymysql_connection` | `📝<remove-pymysql-connection>`          |
| `ddtrace.contrib.requests`    | `legacy`                                | `📝<remove-requests-legacy-distributed>` |
| `ddtrace.contrib.redis`       | `tracers.get_traced_redis`              | `📝<remove-redis-traced>`                |
| `ddtrace.contrib.redis`       | `tracers.get_traced_redis_from`         | `📝<remove-redis-traced-from>`           |
| `ddtrace.contrib.sqlite3`     | `connection_factory`                    | `📝<remove-sqlite3-legacy>`              |

##### Removed deprecated modules

These modules have been removed. Many were moved to the internal interface as they were not intended to be used as part of the public interface. In these cases, no action is provided for upgrading. In a few cases, other modules are provided as alternatives to maintain functionality. See the notes for more information.

| Module                      | Note                                   |
|-----------------------------|----------------------------------------|
| `ddtrace.compat`            | `📝<remove-ddtrace-compat>`            |
| `ddtrace.contrib.util`      | `📝<remove-contrib-util>`              |
| `ddtrace.encoding`          | `📝<remove-ddtrace-encoding>`          |
| `ddtrace.ext.errors`        | `📝<remove-ext-errors>`                |
| `ddtrace.ext.priority`      | `📝<remove-ext-priority>`              |
| `ddtrace.ext.system`        | `📝<remove-ext-system>`                |
| `ddtrace.http`              | `📝<remove-http>`                      |
| `ddtrace.monkey`            | `📝<remove-ddtrace-monkey>`            |
| `ddtrace.propagation.utils` | `📝<remove-ddtrace-propagation-utils>` |
| `ddtrace.util`              | `📝<remove-ddtrace-util>`              |
| `ddtrace.utils`             | `📝<remove-ddtrace-utils>`             |

### New Features

- Add `Span.get_tags` and `Span.get_metrics`.

- aiohttp: add client integration. This integration traces requests made using the aiohttp client and includes support for distributed tracing. See [the documentation](https://ddtrace.readthedocs.io/en/stable/integrations.html#aiohttp) for more information.

- aiohttp_jinja2: move into new integration. Formerly the aiohttp_jinja2 instrumentation was enabled using the aiohttp integration. Use `patch(aiohttp_jinja2=True)` instead of `patch(aiohttp=True)`. To support legacy behavior `patch(aiohttp=True)` will still enable aiohttp_jinja2.

- asyncpg: add integration supporting v0.18.0 and above. See `the docs<asyncpg>` for more information.

- fastapi: add support for tracing `fastapi.routing.serialize_response`.

  This will give an insight into how much time is spent calling `jsonable_encoder` within a given request. This does not provide visibility into how long it takes for `Response.render`/`json.dumps`.

- Add support to reuse HTTP connections when sending trace payloads to the agent. This feature is disabled by default. Set `DD_TRACE_WRITER_REUSE_CONNECTIONS=true` to enable this feature.

- MySQLdb: Added optional tracing for MySQLdb.connect, using the configuration option `here<mysqldb_config_trace_connect>`.

- The profiler now supports profiling `asyncio.Lock` objects.

- psycopg2: add option to enable tracing `psycopg2.connect` method.

  See our `psycopg2` documentation for more information.

- Add support for injecting and extracting B3 propagation headers.

  See `DD_TRACE_PROPAGATION_STYLE_EXTRACT <dd-trace-propagation-style-extract>` and `DD_TRACE_PROPAGATION_STYLE_INJECT <dd-trace-propagation-style-inject>` configuration documentation to enable.

### Upgrade Notes

- <div id="remove-default-sampler">

  The deprecated attribute `ddtrace.Sampler.default_sampler` is removed.

  </div>

- Spans started after `Tracer.shutdown()` has been called will no longer be sent to the Datadog Agent.

- <div id="disable-basic-config-call-by-default">

  Default value of `DD_CALL_BASIC_CONFIG` was updated from `True` to `False`. Call `logging.basicConfig()` to configure logging in your application.

  </div>

- aiohttp_jinja2: use `patch(aiohttp_jinja2=True)` instead of `patch(aiohttp=True)` for enabling/disabling the integration.

- <div id="remove-config-httpserver">

  `ddtrace.settings.Config.HTTPServerConfig` is removed.

  </div>

- <div id="remove-cassandra-traced">

  cassandra: `get_traced_cassandra` is removed. Use `ddtrace.patch(cassandra=True)` or `ddtrace.patch_all()` instead.

  </div>

- <div id="remove-celery-patch-task">

  celery: `ddtrace.contrib.celery.patch_task` is removed. Use `ddtrace.patch(celery=True)` or `ddtrace.patch_all()` instead.

  </div>

- <div id="remove-celery-unpatch-task">

  celery: `ddtrace.contrib.celery.unpatch_task` is removed. Use `ddtrace.contrib.celery.unpatch()` instead.

  </div>

- <div id="remove-clone-context">

  `ddrace.context.Context.clone` is removed. This is no longer needed since the tracer now supports asynchronous frameworks out of the box.

  </div>

- `ddtrace.constants.FILTERS_KEY` is removed.

- `ddtrace.constants.NUMERIC_TAGS` is removed.

- `ddtrace.constants.LOG_SPAN_KEY` is removed.

- <div id="remove-contrib-util">

  The deprecated module `ddtrace.contrib.util` is removed.

  </div>

- <div id="remove-ddtrace-compat">

  The deprecated module `ddtrace.compat` is removed.

  </div>

- <div id="remove-ddtrace-encoding">

  The deprecated module `ddtrace.encoding` is removed.

  </div>

- <div id="remove-http">

  The deprecated modules `ddtrace.http` and `ddtrace.http.headers` are removed. Use `ddtrace.contrib.trace_utils.set_http_meta` to store request and response headers on a span.

  </div>

- <div id="remove-ddtrace-install-excepthooks">

  Remove deprecated `ddtrace.install_excepthook`.

  </div>

- <div id="remove-ddtrace-uninstall-excepthooks">

  Remove deprecated `ddtrace.uninstall_excepthook`.

  </div>

- <div id="remove-ddtrace-monkey">

  The deprecated module `ddtrace.monkey` is removed. Use `ddtrace.patch <ddtrace.patch>` or `ddtrace.patch_all <ddtrace.patch_all>` instead.

  </div>

- <div id="remove-ddtrace-propagation-utils">

  The deprecated module `ddtrace.propagation.utils` is removed.

  </div>

- <div id="remove-ddtrace-utils">

  The deprecated module `ddtrace.utils` and its submodules are removed:
  - `ddtrace.utils.attr`
  - `ddtrace.utils.attrdict`
  - `ddtrace.utils.cache`
  - `ddtrace.utils.config`
  - `ddtrace.utils.deprecation`
  - `ddtrace.utils.formats`
  - `ddtrace.utils.http`
  - `ddtrace.utils.importlib`
  - `ddtrace.utils.time`
  - `ddtrace.utils.version`
  - `ddtrace.utils.wrappers`

  </div>

- <div id="remove-tracer-sampler">

  `ddtrace.Tracer.sampler` is removed.

  </div>

- <div id="remove-tracer-priority-sampler">

  `ddtrace.Tracer.priority_sampler` is removed.

  </div>

- <div id="remove-tracer-tags">

  `ddtrace.Tracer.tags` is removed. Use the environment variable `DD_TAGS<dd-tags>` to set the global tags instead.

  </div>

- <div id="remove-tracer-log">

  `ddtrace.Tracer.log` was removed.

  </div>

- <div id="remove-ext-errors">

  The deprecated module `ddtrace.ext.errors` is removed. Use the `ddtrace.constants` module instead:

      from ddtrace.constants import ERROR_MSG
      from ddtrace.constants import ERROR_STACK
      from ddtrace.constants import ERROR_TYPE

  </div>

- <div id="remove-ext-priority">

  The deprecated module `ddtrace.ext.priority` is removed. Use the `ddtrace.constants` module instead for setting sampling priority tags:

      from ddtrace.constants import USER_KEEP
      from ddtrace.constants import USER_REJECT

  </div>

- <div id="remove-ext-system">

  The deprecated module `ddtrace.ext.system` is removed. Use `ddtrace.constants.PID` instead.

  </div>

- <div id="remove-helpers">

  The deprecated method `ddtrace.helpers.get_correlation_ids` is removed. Use `ddtrace.Tracer.get_log_correlation_context` instead.

  </div>

- <div id="remove-legacy-service-name-envs">

  The legacy environment variables `DD_SERVICE_NAME` and `DATADOG_SERVICE_NAME` are removed. Use `DD_SERVICE` instead.

  </div>

- <div id="remove-mongoengine-traced">

  mongoengine: The deprecated method `ddtrace.contrib.mongoengine.trace_mongoengine` is removed. Use `ddtrace.patch(mongoengine=True)` or `ddtrace.patch()` instead.

  </div>

- <div id="remove-mysql-legacy">

  mysql: The deprecated method `ddtrace.contrib.mysql.get_traced_mysql_connection` is removed. Use `ddtrace.patch(mysql=True)` or `ddtrace.patch_all()` instead.

  </div>

- <div id="remove-pin-app">

  `Pin.app` is removed.

  </div>

- <div id="remove-pin-apptype">

  `Pin.app_type` is removed.

  </div>

- <div id="remove-psycopg-legacy">

  psycopg: `ddtrace.contrib.psycopg.connection_factory` is removed. Use `ddtrace.patch(psycopg=True)` or `ddtrace.patch_all()` instead.

  </div>

- <div id="remove-requests-legacy-distributed">

  requests: The legacy distributed tracing configuration is removed. Use `ddtrace.config.requests['distributed_tracing']<requests-config-distributed-tracing>` instead.

  </div>

- <div id="remove-span-meta">

  `ddtrace.Span.meta` is removed. Use `ddtrace.Span.get_tag` and `ddtrace.Span.set_tag` instead.

  </div>

- <div id="remove-span-metrics">

  `ddtrace.Span.metrics` is removed. Use `ddtrace.Span.get_metric` and `ddtrace.Span.set_metric` instead.

  </div>

- <div id="remove-span-pprint">

  `ddtrace.Span.pprint` is removed.

  </div>

- <div id="remove-span-set-meta">

  `ddtrace.Span.set_meta` is removed. Use `ddtrace.Span.set_tag` instead.

  </div>

- <div id="remove-span-set-metas">

  `ddtrace.Span.set_metas` is removed. Use `ddtrace.Span.set_tags` instead.

  </div>

- `Span.to_dict` is removed.

- <div id="remove-span-tracer">

  `Span.tracer` is removed.

  </div>

- <div id="remove-span-init-tracer">

  The deprecated <span class="title-ref">tracer</span> argument is removed from `ddtrace.Span.__init__`.

  </div>

- <div id="remove-sqlite3-legacy">

  sqlite3: `ddtrace.contrib.sqlite3.connection_factory` is removed. Use `ddtrace.patch(sqlite3=True)` or `ddtrace.patch_all()` instead.

  </div>

- <div id="remove-tracer-debug-logging">

  Remove deprecated attribute `ddtrace.Tracer.debug_logging`. Set the logging level for the `ddtrace.tracer` logger instead:

      import logging
      log = logging.getLogger("ddtrace.tracer")
      log.setLevel(logging.DEBUG)

  </div>

- <div id="remove-tracer-call">

  `ddtrace.Tracer.__call__` is removed.

  </div>

- <div id="remove-tracer-global-excepthook">

  `ddtrace.Tracer.global_excepthook` is removed.

  </div>

- <div id="remove-tracer-get-call-context">

  `ddtrace.Tracer.get_call_context` is removed. Use `ddtrace.Tracer.current_trace_context` instead.

  </div>

- <div id="remove-tracer-set-service-info">

  `ddtrace.Tracer.set_service_info` is removed.

  </div>

- <div id="remove-tracer-writer">

  `ddtrace.Tracer.writer` is removed. To force flushing of buffered traces to the agent, use `ddtrace.Tracer.flush` instead.

  </div>

- `ddtrace.warnings.DDTraceDeprecationWarning` is removed.

- `DD_TRACE_RAISE_DEPRECATIONWARNING` environment variable is removed.

- <div id="remove-datadog-envs">

  The environment variables prefixed with `DATADOG_` are removed. Use environment variables prefixed with `DD_` instead.

  </div>

- <div id="remove-logging-env">

  The environment variable `DD_LOGGING_RATE_LIMIT` is removed. Use `DD_TRACE_LOGGING_RATE` instead.

  </div>

- <div id="remove-partial-flush-enabled-env">

  The environment variable `DD_TRACER_PARTIAL_FLUSH_ENABLED` is removed. Use `DD_TRACE_PARTIAL_FLUSH_ENABLED` instead.

  </div>

- <div id="remove-partial-flush-min-envs">

  The environment variable `DD_TRACER_PARTIAL_FLUSH_MIN_SPANS` is removed. Use `DD_TRACE_PARTIAL_FLUSH_MIN_SPANS` instead.

  </div>

- <div id="remove-ddtrace-util">

  `ddtrace.util` is removed.

  </div>

- <div id="remove-span-types-enum">

  `ddtrace.ext.SpanTypes` is no longer an `Enum`. Use `SpanTypes.<TYPE>` instead of `SpanTypes.<TYPE>.value`.

  </div>

- `Tracer.write` has been removed.

- <div id="remove-flask-middleware">

  Removed deprecated middleware `ddtrace.contrib.flask.middleware.py:TraceMiddleware`. Use `ddtrace.patch_all` or `ddtrace.patch` instead.

  </div>

- <div id="remove-pymongo-client">

  Removed deprecated function `ddtrace.contrib.pymongo.patch.py:trace_mongo_client`. Use `ddtrace.patch_all` or `ddtrace.patch` instead.

  </div>

- <div id="remove-pymysql-connection">

  Removed deprecated function `ddtrace.contrib.pymysql.tracers.py:get_traced_pymysql_connection`. Use `ddtrace.patch_all` or `ddtrace.patch` instead.

  </div>

- <div id="remove-redis-traced">

  Removed deprecated function `ddtrace.contrib.redis.tracers.py:get_traced_redis`. Use `ddtrace.patch_all` or `ddtrace.patch` instead.

  </div>

- <div id="remove-redis-traced-from">

  Removed deprecated function `ddtrace.contrib.redis.tracers.py:get_traced_redis_from`. Use `ddtrace.patch_all` or `ddtrace.patch` instead.

  </div>

### Deprecation Notes

- <div id="deprecate-basic-config-call">

  `DD_CALL_BASIC_CONFIG` is deprecated.

  </div>

### Bug Fixes

- Fixes deprecation warning for `asyncio.coroutine` decorator.

- botocore: fix incorrect context propagation message attribute types for SNS. This addresses [Datadog/serverless-plugin-datadog#232](https://github.com/DataDog/serverless-plugin-datadog/issues/232)

- aiohttp: fix issue causing `ddtrace.contrib.aiohttp_jinja2.patch` module to be imported instead of the `patch()` function.

- botocore: omit `SecretBinary` and `SecretString` from span metadata for calls to Secrets Manager.

- tracing/internal: fix encoding of propagated internal tags.

- Fix issue building `ddtrace` from source on macOS 12.

- Fix issue building `ddtrace` for the Pyston Python implementation by not building the `_memalloc` extension anymore when using Pyston.

- `tracer.get_log_correlation_context()`: use active context in addition to
  active span. Formerly just the span was used and this would break cross execution log correlation as a context object is used for the propagation.

- opentracer: update `set_tag` and `set_operation_name` to return a
  reference to the span to match the OpenTracing spec.

- The CPU profiler now reports the main thread CPU usage even when asyncio tasks are running.

- Fixes wrong numbers of memory allocation being reported in the memory profiler.

- pymongo: fix `write_command` being patched with the wrong method signature.

### Other Notes

- tracing/internal: disable Datadog internal tag propagation

---

## v1.0.3

### Bug Fixes

- Set required header to indicate top level span computation is done in the client to the Datadog agent. This fixes an issue where spans were erroneously being marked as top level when partial flushing or in certain asynchronous applications.

  The impact of this bug is the unintended computation of stats for non-top level spans.

---

## v1.0.2

### Bug Fixes

- Fixes deprecation warning for `asyncio.coroutine` decorator.

---

## v1.0.1

### Bug Fixes

- Fix issue building `ddtrace` for the Pyston Python implementation by not building the `_memalloc` extension anymore when using Pyston.

- `tracer.get_log_correlation_context()`: use active context in addition to
  active span. Formerly just the span was used and this would break cross execution log correlation as a context object is used for the propagation.

- The CPU profiler now reports the main thread CPU usage even when asyncio tasks are running.

---

## v1.0.0

### Prelude

The Datadog APM Python team is happy to announce the release of v1.0.0 of ddtrace. This release introduces a formal `versioning policy<versioning>` that simplifies the public `interface<versioning_interfaces>` and defines a `release version policy<versioning_release>` for backwards compatible and incompatible changes to the public interface.

The v1.0.0 release is an important milestone for the library as it has grown substantially in scope. The first commit to the library was made on June 20, 2016. Nearly sixty minor releases later, the library now includes over sixty integrations for libraries. And the library has expanded from Tracing to support the Continuous Profiler and CI Visibility.

<div class="important">

<div class="title">

Important

</div>

Before upgrading to v1.0.0, we recommend users install `ddtrace>=0.60.0,<1.0.0` and enable deprecation warnings. All removals to the library interface and environment variables were deprecated on 0.x branch. Consult `Upgrade 0.x<upgrade-0.x>` for recommendations on migrating from the 0.x release branch.

</div>

<div class="note">

<div class="title">

Note

</div>

The changes to environment variables apply only to the configuration of the ddtrace library and not the Datadog Agent.

</div>

#### Upgrading summary

##### Functionality changes

The default logging configuration functionality of `ddtrace-run` has changed to address conflicts with application logging configuration. See `note on the new default behavior<disable-basic-config-call-by-default>` and `note on deprecation<deprecate-basic-config-call>` for future removal.

##### Removed legacy environment variables

These environment variables have been removed. In all cases the same functionality is provided by other environment variables and replacements are provided as recommended actions for upgrading.

| Variable                            | Replacement                        | Note                                   |
|-------------------------------------|------------------------------------|----------------------------------------|
| `DATADOG_` prefix                   | `DD_` prefix                       | `📝<remove-datadog-envs>`              |
| `DATADOG_SERVICE_NAME`              | `DD_SERVICE`                       | `📝<remove-legacy-service-name-envs>`  |
| `DD_LOGGING_RATE_LIMIT`             | `DD_TRACE_LOGGING_RATE`            | `📝<remove-logging-env>`               |
| `DD_TRACER_PARTIAL_FLUSH_ENABLED`   | `DD_TRACE_PARTIAL_FLUSH_ENABLED`   | `📝<remove-partial-flush-enabled-env>` |
| `DD_TRACER_PARTIAL_FLUSH_MIN_SPANS` | `DD_TRACE_PARTIAL_FLUSH_MIN_SPANS` | `📝<remove-partial-flush-min-envs>`    |

##### Removed legacy tracing interfaces

These methods and module attributes have been removed. Where the same functionality is provided by a different public method or module attribute, a recommended action is provided for upgrading. In a few limited cases, because the interface was no longer used or had been moved to the internal interface, it was removed and so no action is provided for upgrading.

| Module             | Method/Attribute           | Note                                  |
|--------------------|----------------------------|---------------------------------------|
| `ddtrace.context`  | `Context.clone`            | `📝<remove-clone-context>`            |
| `ddtrace.pin`      | `Pin.app`                  | `📝<remove-pin-app>`                  |
|                    | `Pin.app_type`             | `📝<remove-pin-apptype>`              |
| `ddtrace.sampler`  | `Sampler.default_sampler`  | `📝<remove-default-sampler>`          |
| `ddtrace.span`     | `Span.tracer`              | `📝<remove-span-tracer>`              |
|                    | `Span.__init__(tracer=)`   | `📝<remove-span-init-tracer>`         |
|                    | `Span.meta`                | `📝<remove-span-meta>`                |
|                    | `Span.metrics`             | `📝<remove-span-metrics>`             |
|                    | `Span.set_meta`            | `📝<remove-span-set-meta>`            |
|                    | `Span.set_metas`           | `📝<remove-span-set-metas>`           |
|                    | `Span.pprint`              | `📝<remove-span-pprint>`              |
| `ddtrace.tracer`   | `Tracer.debug_logging`     | `📝<remove-tracer-debug-logging>`     |
|                    | `Tracer.get_call_context`  | `📝<remove-tracer-get-call-context>`  |
|                    | `Tracer.tags`              | `📝<remove-tracer-tags>`              |
|                    | `Tracer.writer`            | `📝<remove-tracer-writer>`            |
|                    | `Tracer.__call__`          | `📝<remove-tracer-call>`              |
|                    | `Tracer.global_excepthook` | `📝<remove-tracer-global-excepthook>` |
|                    | `Tracer.log`               | `📝<remove-tracer-log>`               |
|                    | `Tracer.priority_sampler`  | `📝<remove-tracer-priority-sampler>`  |
|                    | `Tracer.sampler`           | `📝<remove-tracer-sampler>`           |
|                    | `Tracer.set_service_info`  | `📝<remove-tracer-set-service-info>`  |
| `ddtrace.ext`      | `SpanTypes`                | `📝<remove-span-types-enum>`          |
| `ddtrace.helpers`  | `get_correlation_ids`      | `📝<remove-helpers>`                  |
| `ddtrace.settings` | `Config.HTTPServerConfig`  | `📝<remove-config-httpserver>`        |

##### Removed legacy integration tracing

These tracing functions in integrations were no longer used for automatic instrumentation so have been removed. Any manual instrumentation code in an application will need to be replaced with `ddtrace.patch_all` or `ddtrace.patch` when upgrading.

| Module                        | Function/Class                          |                                          |
|-------------------------------|-----------------------------------------|------------------------------------------|
| `ddtrace.contrib.cassandra`   | `get_traced_cassandra`                  | `📝<remove-cassandra-traced>`            |
| `ddtrace.contrib.celery`      | `patch_task`                            | `📝<remove-celery-patch-task>`           |
| `ddtrace.contrib.celery`      | `unpatch_task`                          | `📝<remove-celery-unpatch-task>`         |
| `ddtrace.contrib.flask`       | `middleware.TraceMiddleware`            | `📝<remove-flask-middleware>`            |
| `ddtrace.contrib.mongoengine` | `trace_mongoengine`                     | `📝<remove-mongoengine-traced>`          |
| `ddtrace.contrib.mysql`       | `get_traced_mysql_connection`           | `📝<remove-mysql-legacy>`                |
| `ddtrace.contrib.psycopg`     | `connection_factory`                    | `📝<remove-psycopg-legacy>`              |
| `ddtrace.contrib.pymongo`     | `patch.trace_mongo_client`              | `📝<remove-pymongo-client>`              |
| `ddtrace.contrib.pymysql`     | `tracers.get_traced_pymysql_connection` | `📝<remove-pymysql-connection>`          |
| `ddtrace.contrib.requests`    | `legacy`                                | `📝<remove-requests-legacy-distributed>` |
| `ddtrace.contrib.redis`       | `tracers.get_traced_redis`              | `📝<remove-redis-traced>`                |
| `ddtrace.contrib.redis`       | `tracers.get_traced_redis_from`         | `📝<remove-redis-traced-from>`           |
| `ddtrace.contrib.sqlite3`     | `connection_factory`                    | `📝<remove-sqlite3-legacy>`              |

##### Removed deprecated modules

These modules have been removed. Many were moved to the internal interface as they were not intended to be used as part of the public interface. In these cases, no action is provided for upgrading. In a few cases, other modules are provided as alternatives to maintain functionality. See the notes for more information.

| Module                      | Note                                   |
|-----------------------------|----------------------------------------|
| `ddtrace.compat`            | `📝<remove-ddtrace-compat>`            |
| `ddtrace.contrib.util`      | `📝<remove-contrib-util>`              |
| `ddtrace.encoding`          | `📝<remove-ddtrace-encoding>`          |
| `ddtrace.ext.errors`        | `📝<remove-ext-errors>`                |
| `ddtrace.ext.priority`      | `📝<remove-ext-priority>`              |
| `ddtrace.ext.system`        | `📝<remove-ext-system>`                |
| `ddtrace.http`              | `📝<remove-http>`                      |
| `ddtrace.monkey`            | `📝<remove-ddtrace-monkey>`            |
| `ddtrace.propagation.utils` | `📝<remove-ddtrace-propagation-utils>` |
| `ddtrace.util`              | `📝<remove-ddtrace-util>`              |
| `ddtrace.utils`             | `📝<remove-ddtrace-utils>`             |

### New Features

- Add `Span.get_tags` and `Span.get_metrics`.
- aiohttp: add client integration. This integration traces requests made using the aiohttp client and includes support for distributed tracing. See [the documentation](https://ddtrace.readthedocs.io/en/stable/integrations.html#aiohttp) for more information.
- aiohttp_jinja2: move into new integration. Formerly the aiohttp_jinja2 instrumentation was enabled using the aiohttp integration. Use `patch(aiohttp_jinja2=True)` instead of `patch(aiohttp=True)`. To support legacy behavior `patch(aiohttp=True)` will still enable aiohttp_jinja2.
- asyncpg: add integration supporting v0.18.0 and above. See `the docs<asyncpg>` for more information.

### Upgrade Notes

- <div id="remove-default-sampler">

  The deprecated attribute `ddtrace.Sampler.default_sampler` is removed.

  </div>

- Spans started after `Tracer.shutdown()` has been called will no longer be sent to the Datadog Agent.

- <div id="disable-basic-config-call-by-default">

  Default value of `DD_CALL_BASIC_CONFIG` was updated from `True` to `False`. Call `logging.basicConfig()` to configure logging in your application.

  </div>

- aiohttp_jinja2: use `patch(aiohttp_jinja2=True)` instead of `patch(aiohttp=True)` for enabling/disabling the integration.

- <div id="remove-config-httpserver">

  `ddtrace.settings.Config.HTTPServerConfig` is removed.

  </div>

- <div id="remove-cassandra-traced">

  cassandra: `get_traced_cassandra` is removed. Use `ddtrace.patch(cassandra=True)` or `ddtrace.patch_all()` instead.

  </div>

- <div id="remove-celery-patch-task">

  celery: `ddtrace.contrib.celery.patch_task` is removed. Use `ddtrace.patch(celery=True)` or `ddtrace.patch_all()` instead.

  </div>

- <div id="remove-celery-unpatch-task">

  celery: `ddtrace.contrib.celery.unpatch_task` is removed. Use `ddtrace.contrib.celery.unpatch()` instead.

  </div>

- <div id="remove-clone-context">

  `ddrace.context.Context.clone` is removed. This is no longer needed since the tracer now supports asynchronous frameworks out of the box.

  </div>

- `ddtrace.constants.FILTERS_KEY` is removed.

- `ddtrace.constants.NUMERIC_TAGS` is removed.

- `ddtrace.constants.LOG_SPAN_KEY` is removed.

- <div id="remove-contrib-util">

  The deprecated module `ddtrace.contrib.util` is removed.

  </div>

- <div id="remove-ddtrace-compat">

  The deprecated module `ddtrace.compat` is removed.

  </div>

- <div id="remove-ddtrace-encoding">

  The deprecated module `ddtrace.encoding` is removed.

  </div>

- <div id="remove-http">

  The deprecated modules `ddtrace.http` and `ddtrace.http.headers` are removed. Use `ddtrace.contrib.trace_utils.set_http_meta` to store request and response headers on a span.

  </div>

- <div id="remove-ddtrace-install-excepthooks">

  Remove deprecated `ddtrace.install_excepthook`.

  </div>

- <div id="remove-ddtrace-uninstall-excepthooks">

  Remove deprecated `ddtrace.uninstall_excepthook`.

  </div>

- <div id="remove-ddtrace-monkey">

  The deprecated module `ddtrace.monkey` is removed. Use `ddtrace.patch <ddtrace.patch>` or `ddtrace.patch_all <ddtrace.patch_all>` instead.

  </div>

- <div id="remove-ddtrace-propagation-utils">

  The deprecated module `ddtrace.propagation.utils` is removed.

  </div>

- <div id="remove-ddtrace-utils">

  The deprecated module `ddtrace.utils` and its submodules are removed:
  - `ddtrace.utils.attr`
  - `ddtrace.utils.attrdict`
  - `ddtrace.utils.cache`
  - `ddtrace.utils.config`
  - `ddtrace.utils.deprecation`
  - `ddtrace.utils.formats`
  - `ddtrace.utils.http`
  - `ddtrace.utils.importlib`
  - `ddtrace.utils.time`
  - `ddtrace.utils.version`
  - `ddtrace.utils.wrappers`

  </div>

- <div id="remove-tracer-sampler">

  `ddtrace.Tracer.sampler` is removed.

  </div>

- <div id="remove-tracer-priority-sampler">

  `ddtrace.Tracer.priority_sampler` is removed.

  </div>

- <div id="remove-tracer-tags">

  `ddtrace.Tracer.tags` is removed. Use the environment variable `DD_TAGS<dd-tags>` to set the global tags instead.

  </div>

- <div id="remove-tracer-log">

  `ddtrace.Tracer.log` was removed.

  </div>

- <div id="remove-ext-errors">

  The deprecated module `ddtrace.ext.errors` is removed. Use the `ddtrace.constants` module instead:

      from ddtrace.constants import ERROR_MSG
      from ddtrace.constants import ERROR_STACK
      from ddtrace.constants import ERROR_TYPE

  </div>

- <div id="remove-ext-priority">

  The deprecated module `ddtrace.ext.priority` is removed. Use the `ddtrace.constants` module instead for setting sampling priority tags:

      from ddtrace.constants import USER_KEEP
      from ddtrace.constants import USER_REJECT

  </div>

- <div id="remove-ext-system">

  The deprecated module `ddtrace.ext.system` is removed. Use `ddtrace.constants.PID` instead.

  </div>

- <div id="remove-helpers">

  The deprecated method `ddtrace.helpers.get_correlation_ids` is removed. Use `ddtrace.Tracer.get_log_correlation_context` instead.

  </div>

- <div id="remove-legacy-service-name-envs">

  The legacy environment variables `DD_SERVICE_NAME` and `DATADOG_SERVICE_NAME` are removed. Use `DD_SERVICE` instead.

  </div>

- <div id="remove-mongoengine-traced">

  mongoengine: The deprecated method `ddtrace.contrib.mongoengine.trace_mongoengine` is removed. Use `ddtrace.patch(mongoengine=True)` or `ddtrace.patch()` instead.

  </div>

- <div id="remove-mysql-legacy">

  mysql: The deprecated method `ddtrace.contrib.mysql.get_traced_mysql_connection` is removed. Use `ddtrace.patch(mysql=True)` or `ddtrace.patch_all()` instead.

  </div>

- <div id="remove-pin-app">

  `Pin.app` is removed.

  </div>

- <div id="remove-pin-apptype">

  `Pin.app_type` is removed.

  </div>

- <div id="remove-psycopg-legacy">

  psycopg: `ddtrace.contrib.psycopg.connection_factory` is removed. Use `ddtrace.patch(psycopg=True)` or `ddtrace.patch_all()` instead.

  </div>

- <div id="remove-requests-legacy-distributed">

  requests: The legacy distributed tracing configuration is removed. Use `ddtrace.config.requests['distributed_tracing']<requests-config-distributed-tracing>` instead.

  </div>

- <div id="remove-span-meta">

  `ddtrace.Span.meta` is removed. Use `ddtrace.Span.get_tag` and `ddtrace.Span.set_tag` instead.

  </div>

- <div id="remove-span-metrics">

  `ddtrace.Span.metrics` is removed. Use `ddtrace.Span.get_metric` and `ddtrace.Span.set_metric` instead.

  </div>

- <div id="remove-span-pprint">

  `ddtrace.Span.pprint` is removed.

  </div>

- <div id="remove-span-set-meta">

  `ddtrace.Span.set_meta` is removed. Use `ddtrace.Span.set_tag` instead.

  </div>

- <div id="remove-span-set-metas">

  `ddtrace.Span.set_metas` is removed. Use `ddtrace.Span.set_tags` instead.

  </div>

- `Span.to_dict` is removed.

- <div id="remove-span-tracer">

  `Span.tracer` is removed.

  </div>

- <div id="remove-span-init-tracer">

  The deprecated <span class="title-ref">tracer</span> argument is removed from `ddtrace.Span.__init__`.

  </div>

- <div id="remove-sqlite3-legacy">

  sqlite3: `ddtrace.contrib.sqlite3.connection_factory` is removed. Use `ddtrace.patch(sqlite3=True)` or `ddtrace.patch_all()` instead.

  </div>

- <div id="remove-tracer-debug-logging">

  Remove deprecated attribute `ddtrace.Tracer.debug_logging`. Set the logging level for the `ddtrace.tracer` logger instead:

      import logging
      log = logging.getLogger("ddtrace.tracer")
      log.setLevel(logging.DEBUG)

  </div>

- <div id="remove-tracer-call">

  `ddtrace.Tracer.__call__` is removed.

  </div>

- <div id="remove-tracer-global-excepthook">

  `ddtrace.Tracer.global_excepthook` is removed.

  </div>

- <div id="remove-tracer-get-call-context">

  `ddtrace.Tracer.get_call_context` is removed. Use `ddtrace.Tracer.current_trace_context` instead.

  </div>

- <div id="remove-tracer-set-service-info">

  `ddtrace.Tracer.set_service_info` is removed.

  </div>

- <div id="remove-tracer-writer">

  `ddtrace.Tracer.writer` is removed. To force flushing of buffered traces to the agent, use `ddtrace.Tracer.flush` instead.

  </div>

- `ddtrace.warnings.DDTraceDeprecationWarning` is removed.

- `DD_TRACE_RAISE_DEPRECATIONWARNING` environment variable is removed.

- <div id="remove-datadog-envs">

  The environment variables prefixed with `DATADOG_` are removed. Use environment variables prefixed with `DD_` instead.

  </div>

- <div id="remove-logging-env">

  The environment variable `DD_LOGGING_RATE_LIMIT` is removed. Use `DD_TRACE_LOGGING_RATE` instead.

  </div>

- <div id="remove-partial-flush-enabled-env">

  The environment variable `DD_TRACER_PARTIAL_FLUSH_ENABLED` is removed. Use `DD_TRACE_PARTIAL_FLUSH_ENABLED` instead.

  </div>

- <div id="remove-partial-flush-min-envs">

  The environment variable `DD_TRACER_PARTIAL_FLUSH_MIN_SPANS` is removed. Use `DD_TRACE_PARTIAL_FLUSH_MIN_SPANS` instead.

  </div>

- <div id="remove-ddtrace-util">

  `ddtrace.util` is removed.

  </div>

- <div id="remove-span-types-enum">

  `ddtrace.ext.SpanTypes` is no longer an `Enum`. Use `SpanTypes.<TYPE>` instead of `SpanTypes.<TYPE>.value`.

  </div>

- `Tracer.write` has been removed.

- <div id="remove-flask-middleware">

  Removed deprecated middleware `ddtrace.contrib.flask.middleware.py:TraceMiddleware`. Use `ddtrace.patch_all` or `ddtrace.patch` instead.

  </div>

- <div id="remove-pymongo-client">

  Removed deprecated function `ddtrace.contrib.pymongo.patch.py:trace_mongo_client`. Use `ddtrace.patch_all` or `ddtrace.patch` instead.

  </div>

- <div id="remove-pymysql-connection">

  Removed deprecated function `ddtrace.contrib.pymysql.tracers.py:get_traced_pymysql_connection`. Use `ddtrace.patch_all` or `ddtrace.patch` instead.

  </div>

- <div id="remove-redis-traced">

  Removed deprecated function `ddtrace.contrib.redis.tracers.py:get_traced_redis`. Use `ddtrace.patch_all` or `ddtrace.patch` instead.

  </div>

- <div id="remove-redis-traced-from">

  Removed deprecated function `ddtrace.contrib.redis.tracers.py:get_traced_redis_from`. Use `ddtrace.patch_all` or `ddtrace.patch` instead.

  </div>

### Deprecation Notes

- <div id="deprecate-basic-config-call">

  `DD_CALL_BASIC_CONFIG` is deprecated.

  </div>

### Bug Fixes

- botocore: fix incorrect context propagation message attribute types for SNS. This addresses [Datadog/serverless-plugin-datadog#232](https://github.com/DataDog/serverless-plugin-datadog/issues/232)
- aiohttp: fix issue causing `ddtrace.contrib.aiohttp_jinja2.patch` module to be imported instead of the `patch()` function.
- tracing/internal: fix encoding of propagated internal tags.
- Fix issue building `ddtrace` from source on macOS 12.
- Fixes wrong numbers of memory allocation being reported in the memory profiler.
- pymongo: fix `write_command` being patched with the wrong method signature.

### Other Notes

- tracing/internal: disable Datadog internal tag propagation

---

## v0.59.2

### Bug Fixes

- botocore: fix incorrect context propagation message attribute types for SNS. This addresses [Datadog/serverless-plugin-datadog#232](https://github.com/DataDog/serverless-plugin-datadog/issues/232)

---

## v0.59.1

### Bug Fixes

- Fix issue building `ddtrace` from source on macOS 12.
- Fixes wrong numbers of memory allocation being reported in the memory profiler.
- pymongo: fix `write_command` being patched with the wrong method signature.

---

## v0.59.0

### Deprecation Notes

- `ddtrace.constants.FILTERS_KEY` is deprecated. Use `settings={"FILTERS": ...}` instead when calling `tracer.configure`.
- `ddtrace.constants.NUMERIC_TAGS` is deprecated.
- `ddtrace.constants.LOG_SPAN_KEY` is deprecated.
- `Pin.app` is deprecated.
- `ddtrace.Span.set_meta` is deprecated. Use `ddtrace.Span.set_tag` instead.
- `ddtrace.Span.set_metas` is deprecated. Use `ddtrace.Span.set_tags` instead.
- `ddtrace.Span.metrics` is deprecated. Use `ddtrace.Span.get_metric` and `ddtrace.Span.set_metric` instead.
- `ddtrace.Span.tracer` is deprecated.
- `ddtrace.Tracer.log` is deprecated. Use `ddtrace.tracer.log` instead.
- `ddtrace.Tracer.sampler` is deprecated.
- `ddtrace.Tracer.priority_sampler` is deprecated.
- `ddtrace.Tracer.tags` is deprecated. Use the environment variable `DD_TAGS<dd-tags>` to set the global tags instead.

### New Features

- The profiler now reports asyncio tasks as part as the task field in profiles. This is enabled by default by replacing the default asyncio loop policy. CPU time, wall time and threading lock times are supported.
- Add tracing support for `sanic>=21.9.0`.

### Bug Fixes

- Fix internal import of deprecated `ddtrace.utils` module.
- Set correct service in logs correlation attributes when a span override the service.
- Fixes import path to not reference `__init__`. This could otherwise be a problem for `mypy`.
- flask: fix resource naming of request span when errors occur in middleware.
- Fix issue when `httpx` service name is `bytes`.
- Fixes build issues on older MacOS versions by updating `libddwaf` to 1.0.18
- pytest: fix unsafe access to xfail reason.

---

## v0.58.5

### Bug Fixes

- tracing/internal: fix encoding of propagated internal tags.

---

## v0.58.4

### Bug Fixes

- Set correct service in logs correlation attributes when a span override the service.

---

## v0.58.3

### Bug Fixes

- Fixes build issues on older MacOS versions by updating `libddwaf` to 1.0.18

---

## v0.58.2

### Bug Fixes

- flask: fix resource naming of request span when errors occur in middleware.
- pytest: fix unsafe access to xfail reason.

---

## v0.58.1

### Bug Fixes

- Fix internal import of deprecated `ddtrace.utils` module.
- Fixes import path to not reference `__init__`. This could otherwise be a problem for `mypy`.

---

## v0.58.0

### Deprecation Notes

- HttpServerConfig is no longer part of the public API.
- `ddtrace.Span.meta` has been deprecated. Use `ddtrace.Span.get_tag` and `ddtrace.Span.set_tag` instead.
- `ddtrace.Span.pprint` is deprecated and will be removed in v1.0.
- `ddtrace.Tracer.writer` is deprecated. To force flushing of buffered traces to the agent, use `ddtrace.Tracer.flush` instead.

### New Features

- botocore: add distributed tracing support for AWS EventBridge, AWS SNS & AWS Kinesis.
- Add `ddtrace.Tracer.agent_trace_url` and `ddtrace.Tracer.flush`.
- Only for CI Visibility (`pytest` integration): remove traces whose root span is not a test.

### Bug Fixes

- Fix application crash on startup when using `channels >= 3.0`.
- Fix parenting of Redis command spans when using aioredis 1.3. Redis spans should now be correctly attributed as child of any active parent spans.
- Fixes incompatibility of wrapped aioredis pipelines in `async with` statements.
- Fixes issue with aioredis when empty pool is not available and execute returns a coroutine instead of a future. When patch tries to add callback for the span using add_done_callback function it crashes because this function is only for futures.
- Escape non-Unicode bytes when decoding aioredis args. This fixes a `UnicodeDecodeError` that can be thrown from the aioredis integration when interacting with binary-encoded data, as is done in channels-redis.
- Ensure `gevent` is automatically patched.
- grpc: ensure grpc.intercept_channel is unpatched properly
- Fix JSON encoding error when a `bytes` string is used for span metadata.
- Profiler raises a typing error when `Span.resource` is unicode on Python 2.7.
- Fix a bug in the heap profiler that could be triggered if more than 2^16 memory items were freed during heap data collection.
- Fix a possible bug in the heap memory profiler that could trigger an overflow when too many allocations were being tracked.
- Fix an issue in the heap profiler where it would iterate on the wrong heap allocation tracker.
- Pymongo instrumentation raises an AttributeError when `tracer.enabled == False`

---

## v0.57.4

### Bug Fixes

- Fix an issue in the heap profiler where it would iterate on the wrong heap allocation tracker.

---

## v0.57.3

### Bug Fixes

- Fix JSON encoding error when a `bytes` string is used for span metadata.
- Fix a bug in the heap profiler that could be triggered if more than 2^16 memory items were freed during heap data collection.
- Fix a possible bug in the heap memory profiler that could trigger an overflow when too many allocations were being tracked.
- Pymongo instrumentation raises an AttributeError when `tracer.enabled == False`

---

## v0.57.2

### Bug Fixes

- Fix application crash on startup when using `channels >= 3.0`.
- Fix parenting of Redis command spans when using aioredis 1.3. Redis spans should now be correctly attributed as child of any active parent spans.
- Fixes issue with aioredis when empty pool is not available and execute returns a coroutine instead of a future. When patch tries to add callback for the span using add_done_callback function it crashes because this function is only for futures.
- Profiler raises a typing error when `Span.resource` is unicode on Python 2.7.

---

## v0.57.1

### Bug Fixes

- Fixes incompatibility of wrapped aioredis pipelines in `async with` statements.
- Escape non-Unicode bytes when decoding aioredis args. This fixes a `UnicodeDecodeError` that can be thrown from the aioredis integration when interacting with binary-encoded data, as is done in channels-redis.
- grpc: ensure grpc.intercept_channel is unpatched properly

---

## v0.57.0

### Deprecation Notes

- `ddtrace.sampler.DatadogSampler.default_sampler` property is deprecated and will be removed in 1.0.
- `ddtrace.propagation.utils` has been deprecated and will be removed in version 1.0.

### New Features

- Add new environment variables to enable/disable django database and cache instrumentation.

  `DD_DJANGO_INSTRUMENT_DATABASES`, `DD_DJANGO_INSTRUMENT_CACHES`

- Add tracing support for the `aioredis` library. Version 1.3+ is fully supported.

- Add django 4.0 support.

- The profiler now automatically injects running greenlets as tasks into the main thread. They can be seen within the wall time profiles.

### Bug Fixes

- The thread safety of the custom buffered encoder was fixed in order to eliminate a potential cause of decoding errors of trace payloads (missing trace data) in the agent.
- Fix handling of Python exceptions during trace encoding. The tracer will no longer silently fail to encode invalid span data and instead log an exception.
- Fix error when calling `concurrent.futures.ThreadPoolExecutor.submit` with `fn` keyword argument.
- Configure a writer thread in a child process after forking based on writer configuration from its parent process.
- Only for CI Visibility (`pytest` integration): Fix calculation of pipeline URL for GitHub Actions.

---

## v0.56.1

### Bug Fixes

- Fix error when calling `concurrent.futures.ThreadPoolExecutor.submit` with `fn` keyword argument.

---

## v0.56.0

### Upgrade Notes

- The aredis integration is now enabled by default.

### Deprecation Notes

- The contents of `monkey.py` have been moved into `_monkey.py` in an effort to internalize the module. Public methods have been imported back into `monkey.py` in order to retain compatibility, but monkey.py will be removed entirely in version 1.0.0.
- The `ddtrace.utils` module and all of its submodules have been copied over into `ddtrace.internal` in an effort to internalize these modules. Their public counterparts will be removed entirely in version 1.0.0.

### New Features

- Profiling now supports tracing greenlets with gevent version prior to 1.3.
- The heap profiler is now enabled by default.
- Add yaaredis ≥ 2.0.0 support.

### Bug Fixes

- Fix Pyramid caller_package level issue which resulted in crashes when starting Pyramid applications. Level now left at default (2).
- Set the correct package name in the Pyramid instrumentation. This should fix an issue where the incorrect package name was being used which would crash the application when trying to do relative imports within Pyramid (e.g. when including routes from a relative path).
- Fix memory leak caused when the tracer is disabled.
- Allow the elasticsearch service name to be overridden using the integration config or the DD_SERVICE_MAPPING environment variable.
- Fixes parsing of `botocore` env variables to ensure they are parsed as booleans.
- Ensure tornado spans are marked as an error if the response status code is 500 \<= x \< 600.

---

## v0.55.4

### Bug Fixes

- Fixes parsing of `botocore` env variables to ensure they are parsed as booleans.
- Ensure tornado spans are marked as an error if the response status code is 500 \<= x \< 600.

---

## v0.55.3

### Bug Fixes

- Fix memory leak caused when the tracer is disabled.

---

## v0.55.2

### Bug Fixes

- Set the correct package name in the Pyramid instrumentation. This should fix an issue where the incorrect package name was being used which would crash the application when trying to do relative imports within Pyramid (e.g. when including routes from a relative path).

---

## v0.55.1

### Bug Fixes

- Fix Pyramid caller_package level issue which resulted in crashes when starting Pyramid applications. Level now left at default (2).

---

## v0.55.0

### Upgrade Notes

- Instead of using error constants from `ddtrace.ext.errors`. Use constants from `ddtrace.constants` module. For example: `ddtrace.ext.errors.ERROR_MSG` -\> `ddtrace.constants.ERROR_MSG`
- Instead of using priority constants from `ddtrace.ext.priority`. Use constants from `ddtrace.constants` module. For Example:: `ddtrace.ext.priority.AUTO_KEEP` -\> `ddtrace.constants.AUTO_KEEP`
- Instead of using system constants from `ddtrace.ext.system`. Use constants from `ddtrace.constants` module. For Example:: `ddtrace.ext.system.PID` -\> `ddtrace.constants.PID`

### Deprecation Notes

- Deprecate DATADOG_TRACE_AGENT_HOSTNAME, DATADOG_TRACE_AGENT_PORT, DATADOG_PRIORITY_SAMPLING, DATADOG_PATCH_MODULES in favor of their DD equivalents.

  \[Deprecated environment variable\] \| \[Recommended environment variable\]

  - For `DATADOG_TRACE_AGENT_HOSTNAME`, use `DD_AGENT_HOST`
  - For `DATADOG_TRACE_AGENT_PORT` use `DD_AGENT_PORT`
  - For `DATADOG_PRIORITY_SAMPLING`, [follow ingestion controls](https://docs.datadoghq.com/tracing/trace_retention_and_ingestion/#recommended-setting-the-global-ingestion-rate-to-100)
  - For `DATADOG_PATCH_MODULES`, use `DD_PATCH_MODULES`

- Moved `ddtrace.ext.errors` constants into the `ddtrace.constants` module. `ddtrace.ext.errors` will be removed in v1.0. Shorthand error constant (MSG,TYPE,STACK) in `ddtrace.ext.errors` will be removed in v1.0. Function `get_traceback()` in ddtrace.ext.errors is now deprecated and will be removed v1.0.

- Moved `ddtrace.ext.priority` constants into `ddtrace.constants` module.

- Moved `ddtrace.ext.system` constants into `ddtrace.constants` module.

### New Features

- Add aredis support \>= 1.1.0
- Add automatic unix domain socket detection for Dogstatsd. The expected path for the socket is `/var/run/datadog/dsd.socket` which if exists, will be used instead of the previous UDP default, `udp://localhost:8125/`. To be used in conjunction with `dogstatsd_socket` in your `datadog.yaml` file, or the `DD_DOGSTATSD_SOCKET` environment variable set on the Datadog agent.
- Add new `DD_TRACE_SAMPLING_RULES` environment variable to override default sampling rules. For Example:: `DD_TRACE_SAMPLING_RULES='[{"sample_rate":0.5,"service":"my-service"}]'`
- Add support for [snowflake-connector-python](https://pypi.org/project/snowflake-connector-python/) \>= 2.0.0. Note that this integration is in beta and is not enabled by default. See the snowflake integration documentation for how to enable.
- Only for CI Visibility (`pytest` integration): include `pytest` version as a tag in the test span.
- Added official support for Python 3.10
- Only for CI Visibility (`pytest` integration): Extract stage and job name from environment data in Azure Pipelines.

### Bug Fixes

- Fixes an issue where all Django function middleware will share the same resource name.
- Fixed an issue with gevent worker processes that caused them to crash and stop.
- Fixes exceptions raised when logging during tracer initialization when `DD_LOGS_INJECTION` is enabled.
- The `ddtrace.utils.wrappers.unwrap` function now raises an error if trying to unwrap a non-wrapped object.
- Only for CI Visibility (`pytest` integration): Fix extraction of branch in GitLab CI.

---

## v0.54.1

### Bug Fixes

- Fixed an issue with gevent worker processes that caused them to crash and stop.
- Fixes exceptions raised when logging during tracer initialization when `DD_LOGS_INJECTION` is enabled.

---

## v0.54.0

### Deprecation Notes

- Deprecate the DATADOG_ENV environment variable in favor of DD_ENV. The use of DD_ENV should follow [Unified Service Tagging](https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging) recommendations.

### New Features

- Add automatic unix domain socket detection for traces. The expected path for the socket is `/var/run/datadog/apm.socket` which if exists, will be used instead of the previous http default, `http://localhost:8126/`. To be used in conjunction with `apm_config.receiver_socket` in your `datadog.yaml` file, or the `DD_APM_RECEIVER_SOCKET` environment variable set on the Datadog agent.
- Update the --info command to be easier to read and provide more helpful information.
- Add support for `DD_PROFILING_ENDPOINT_COLLECTION_ENABLED` env variable to disable endpoint name collection in profiler.
- Add rq integration.
- Tag traces with HTTP headers specified on the `DD_TRACE_HEADER_TAGS` environment variable. Value must be either comma or space separated. e.g. `Host:http.host,User-Agent,http.user_agent` or `referer:http.referer Content-Type:http.content_type`.

### Bug Fixes

- pytest: report exception details directly instead of through a <span class="title-ref">RuntimeWarning</span> exception.
- Fixed the support for Celery workers that fork sub-processes with Python 3.6 and earlier versions.
- Fix the reporting of the allocated memory and the number of allocations in the profiler.
- Fixes cases in which the `test.status` tag of a test span from `pytest` would be missing because `pytest_runtest_makereport` hook is not run, like when `pytest` has an internal error.
- Pin `protobuf` version to `<3.18` for Python \<=3.5 due to support being dropped.
- Make sure that correct endpoint name collected for profiling.

### Other Changes

- Added runtime metrics status and sampling rules to start-up logs.

---

## v0.53.3

### Bug Fixes

- Fixes an issue where all Django function middleware will share the same resource name.

---

## v0.53.2

### Bug Fixes

- Fix the reporting of the allocated memory and the number of allocations in the profiler.

---

## v0.53.1

### Bug Fixes

- Fixed the support for Celery workers that fork sub-processes with Python 3.6 and earlier versions.
- Fixes cases in which the `test.status` tag of a test span from `pytest` would be missing because `pytest_runtest_makereport` hook is not run, like when `pytest` has an internal error.
- Pin `protobuf` version to `<3.18` for Python \<=3.5 due to support being dropped.

---

## v0.53.0

### Upgrade Notes

- Replace DD_TRACER_PARTIAL_FLUSH_ENABLED with DD_TRACE_PARTIAL_FLUSH_ENABLED Replace DD_TRACER_PARTIAL_FLUSH_MIN_SPANS with DD_TRACE_PARTIAL_FLUSH_MIN_SPANS

### Deprecation Notes

- The DD_TRACER_PARTIAL_FLUSH_ENABLED and DD_TRACER_PARTIAL_FLUSH_MIN_SPANS environment variables have been deprecated and will be removed in version 1.0 of the library.

### New Features

- The `ddtrace.Tracer.get_log_correlation_context` method has been added to replace `ddtrace.helpers.get_correlation_ids`. It now returns a dictionary which includes the current span's trace and span ids, as well as the configured service, version, and environment names.
- The gevent tasks are now tracked by the threading lock events

### Bug Fixes

- Fixes an issue where a manually set `django.request` span resource would get overwritten by the integration.
- Pin `setup_requires` dependency `setuptools_scm[toml]>=4,<6.1` to avoid breaking changes.
- The profiler now updates the trace resource when capturing span information with the stack and lock collectors. That means that if the trace resource changes after the profiling events are created, the profiler samples will also be updated. This avoids having trace resource being empty when profiling, e.g., WSGI middleware.

---

## v0.52.2

### Bug Fixes

- Pin `protobuf` version to `<3.18` for Python \<=3.5 due to support being dropped.

---

## v0.52.1

### Bug Fixes

- Pin `setup_requires` dependency `setuptools_scm[toml]>=4,<6.1` to avoid breaking changes.

---

## v0.52.0

### Deprecation Notes

- Removed the `collect_metrics` argument from `Tracer.configure`. See the release notes for v0.49.0 for the migration instructions.

### New Features

- Add tracing support for the `httpx` library. Supported versions `>=0.14.0`.

- ASGI: store the ASGI span in the scope. The span can be retrieved with the
  `ddtrace.contrib.asgi.span_from_scope` function.

- Submit runtime metrics as distribution metrics instead of gauge metrics.

- Support flask-caching (\>= 1.10.0) with the Flask-Cache tracer.

- Only for CI Visibility (<span class="title-ref">pytest</span> integration): It is now possible to specify any of the following git metadata through environment variables:
  - \`DD_GIT_REPOSITORY_URL\`: The url of the repository where the code is stored
  - \`DD_GIT_TAG\`: The tag of the commit, if it has one
  - \`DD_GIT_BRANCH\`: The branch where this commit belongs to
  - \`DD_GIT_COMMIT_SHA\`: The commit hash of the current code
  - \`DD_GIT_COMMIT_MESSAGE\`: Commit message
  - \`DD_GIT_COMMIT_AUTHOR_NAME\`: Commit author name
  - \`DD_GIT_COMMIT_AUTHOR_EMAIL\`: Commit author email
  - \`DD_GIT_COMMIT_AUTHOR_DATE\`: The commit author date (ISO 8601)
  - \`DD_GIT_COMMIT_COMMITTER_NAME\`: Commit committer name
  - \`DD_GIT_COMMIT_COMMITTER_EMAIL\`: Commit committer email
  - \`DD_GIT_COMMIT_COMMITTER_DATE\`: The commit committer date (ISO 8601)

### Bug Fixes

- ASGI: handle decoding errors when extracting headers for trace propagation.

- Corrected some typing annotations for PEP 484 compliance

- Django: add support for version 3.1+ ASGI applications. A different codepath is taken for requests starting in Django 3.1 which led to the top level span not being generated for requests. The fix introduces automatic installation of the ASGI middleware to trace Django requests.

- dogpile.cache: handle both kwargs and args in the wrapper functions (using
  only kwargs would result in an IndexError.

- Fixes an issue with the Django integration where if the `urlconf` changes at any point during the handling of the request then the resource name will only be `<METHOD> 404`. This fix moves resource name resolution to the end of the request.

- Fixes error with tagging non-string Flask view args.

- `werkzeug.exceptions.NotFound` 404 errors are no longer raised and logged as a server error in the Flask integration.

- Fixes type hinting for `**patch_modules` parameter for `patch`/`patch_all` functions.

- Fixes an issue when using the pytest plugin with doctest which raises an `AttributeError` on `DoctestItem`.

- Fixes a bug in the pytest plugin where xfail test cases in a test file with a module-wide skip raises attribute errors and are marked as xfail rather than skipped.

- Fixed the handling of sanic endpoint paths with non-string arguments.

- opentracer: don't override default tracing config for the <span class="title-ref">ENABLED</span>,
  <span class="title-ref">AGENT_HOSTNAME</span>, <span class="title-ref">AGENT_HTTPS</span> or <span class="title-ref">AGENT_PORT</span> settings.

---

## v0.51.3

### Bug Fixes

- Pin `setup_requires` dependency `setuptools_scm[toml]>=4,<6.1` to avoid breaking changes.

---

## v0.51.2

### New Features

- ASGI: store the ASGI span in the scope. The span can be retrieved with the
  `ddtrace.contrib.asgi.span_from_scope` function.

### Bug Fixes

- ASGI: handle decoding errors when extracting headers for trace propagation.
- Corrected some typing annotations for PEP 484 compliance
- Django: add support for version 3.1+ ASGI applications. A different codepath is taken for requests starting in Django 3.1 which led to the top level span not being generated for requests. The fix introduces automatic installation of the ASGI middleware to trace Django requests.
- Fixes error with tagging non-string Flask view args.
- Fixes type hinting for `**patch_modules` parameter for `patch`/`patch_all` functions.
- Fixes a bug in the pytest plugin where xfail test cases in a test file with a module-wide skip raises attribute errors and are marked as xfail rather than skipped.

---

## v0.51.0

### Upgrade Notes

- The legacy Django configuration method (deprecated in 0.34) has been removed.
- botocore: Update trace propagation format for directly invoked Lambda functions. This breaks compatibility with Lambda functions instrumented with datadog-lambda-python \< v41 or datadog-lambda-js \< v3.57.0. Please upgrade datadog-lambda-\* in invoked lambda functions, or engage legacy compatibility mode in one of two ways:
  - ddtrace.config.botocore.invoke_with_legacy_context = True
  - DD_BOTOCORE_INVOKE_WITH_LEGACY_CONTEXT=true

### Deprecation Notes

- `monkey.patch_module` is deprecated.
- `monkey.get_patch_module` is deprecated.

### New Features

- Added support for `jinja2~=3.0.0`.
- The pytest integration now uses the name of the repository being tested as the default test service name.
- Added support for `aiopg~=0.16.0`.
- Add MariaDB integration.
- The profiler now exports active tasks for CPU and wall time profiles.

### Bug Fixes

- Fixes an issue with enabling the runtime worker introduced in v0.49.0 where no runtime metrics were sent to the agent.
- Fix pymongo 3.12.0+ spans not being generated.
- Fixed JSON encoding errors in the pytest plugin for parameterized tests with dictionary parameters with tuple keys. The pytest plugin now always JSON encodes the string representations of test parameters.
- Fixed JSON encoding errors in the pytest plugin for parameterized tests with complex Python object parameters. The pytest plugin now defaults to encoding the string representations of non-JSON serializable test parameters.
- Fix a possible NoneType error in the WSGI middleware start_response method.

---

## v0.50.4

### Bug Fixes

- Fixes a bug in the pytest plugin where xfail test cases in a test file with a module-wide skip raises attribute errors and are marked as xfail rather than skipped.

---

## v0.50.3

### Bug Fixes

- Fixed the handling of sanic endpoint paths with non-string arguments.

---

## v0.50.2

### Bug Fixes

- Fixed JSON encoding errors in the pytest plugin for parameterized tests with dictionary parameters with tuple keys. The pytest plugin now always JSON encodes the string representations of test parameters.

---

## v0.50.1

### Bug Fixes

- Fixed JSON encoding errors in the pytest plugin for parameterized tests with complex Python object parameters. The pytest plugin now defaults to encoding the string representations of non-JSON serializable test parameters.
- Fix pymongo 3.12.0+ spans not being generated.
- Fix a possible NoneType error in the WSGI middleware start_response method.

---

## v0.50.0

### Prelude

Major changes to context management. See the upgrade section for the specifics. Note that only advanced users of the library should be affected by these changes. For the details please refer to the Context section of the docs: <https://ddtrace.readthedocs.io/en/v0.50.0/advanced_usage.html>

### Deprecation Notes

- The reuse of a tracer that has been shut down has been deprecated. A new tracer should be created for generating new traces.
- The deprecated dbapi2 configuration has been removed. The integration-specific configuration should be used instead. Look at the v0.48.0 release notes for migration instructions.

### Upgrade Notes

- `ddtrace.contrib.asyncio`

  - `AsyncioContextProvider` can now return and activate `None`, `Span` or `Context` objects.

- `ddtrace.contrib.gevent`

  - `GeventContextProvider` can now return and activate `None`, `Span` or `Context` objects.

- `ddtrace.contrib.tornado`

  - `TracerStackContext` can now return and activate `None`, `Span` or `Context` objects.

- `ddtrace.context.Context` no longer maintains the active/current span state.

  `get_current_root_span()` has been removed. Use `tracer.current_root_span()` instead. `get_current_span()` has been removed. Use `tracer.current_span()` instead. `add_span()` has been removed. To activate a span in an execution use `tracer.context_provider.activate()` instead. `close_span()` has been removed. To deactivate a span in an execution use `tracer.context_provider.activate()` instead.

- `ddtrace.provider.BaseContextProvider` `active()` now returns `None`, `Span` or `Context` objects. `activate()` now accepts `None`, `Span` or `Context` objects.

- `ddtrace.span.Span`

  - `Span.context` will now return a `Context`

- `ddtrace.tracer.Tracer` `tracer.get_call_context()` will now return a one-off `Context` reference. This is to maintain backwards compatibility with the API but the functionality differs slightly. `tracer.start_span()` passing a `span.context` for `child_of` no longer adds the strong `_parent` reference to the new span.

- Support for MySQL-python has been removed.

- Support for psycopg \< 2.7 has been removed.

### New Features

- Add `DD_CALL_BASIC_CONFIG={true,false}` environment variable to control whether `ddtrace` calls `logging.basicConfig`. By default when using `ddtrace-run` or running in debug mode `logging.basicConfig` is called to ensure there is always a root handler. This has compatibility issues for some logging configurations. `DD_CALL_BASIC_CONFIG=false` can be used to skip calling `logging.basicConfig`. The default value is `true` to maintain existing behavior.
- agent: support URL with a base path
- Automated context management should now work in all asynchronous frameworks that use `contextvars`.
- `tracer.start_span()` now accepts an `activate` argument (default `False`) to allow manual context management.
- `tracer.current_trace_context()` has been added to be used to access the trace context of the active trace.
- A warning has been added to alert when gevent monkey patching is done after ddtrace has been imported.
- Add support for Flask 2
- Added retry logic to the tracer to mitigate potential networking issues, like timeouts or dropped connections.
- Add new `DD_TRACE_AGENT_TIMEOUT_SECONDS` to override the default connection timeout used when sending data to the trace agent. The default is `2.0` seconds.
- The CI tagging for the pytest plugin now includes OS and Python Runtime metadata including system architecture, platform, version, and Python runtime name and version.
- Add new environment variables to configure the internal trace writer.

  `DD_TRACE_WRITER_MAX_BUFFER_SIZE`, `DD_TRACE_WRITER_INTERVAL_SECONDS`, `DD_TRACE_WRITER_MAX_PAYLOAD_SIZE_BYTES`

- The exception profiler now gathers and exports the traces and spans information.
- The pytest plugin now includes support for automatically tagging spans with parameters in parameterized tests.
- The Python heap profiler can now be enabled by setting the `DD_PROFILING_HEAP_ENABLED` environment variable to `1`.

### Bug Fixes

- The OpenTracing `tracer.start_span` method no longer activates spans.
- Datadog active spans will no longer take precedence over OpenTracing active spans.
- django: fix a bug where multiple database backends would not be instrumented.
- django: fix a bug when postgres query is composable sql object.
- A possible memory leak that occurs when tracing across a fork has been fixed. See <https://github.com/DataDog/dd-trace-py/pull/2497> for more information.
- Fix double patching of `pymongo` client topology.
- The shutdown task is re-registered when a tracer is reused after it has been shut down.
- Fixed the optional argument of `Span.finish` to `Optional[float]` instead of `Optional[int]`.
- Fixed the handling of the Django template name tag causing type errors.

- Fixes an issue when trying to manually start the runtime metrics worker:

      AttributeError: module 'ddtrace.internal.runtime' has no attribute 'runtime_metrics'

- sanic: update instrumentation to support version 21.

- Performance of the Celery integration has been improved.

- Fix runtime-id and system.pid tags not being set on distributed traces.

### Other Changes

- The pytest plugin now includes git metadata tags including author name and email as well as commit message from CI provider environments.
- The profiler won't be ignoring its own resource usage anymore and will report it in the profiles.
- The botocore integration excludes AWS endpoint call parameters that have a name ending with `Body` from the set of span tags.

---

## v0.49.4

### Bug Fixes

- Fixes an issue when trying to manually start the runtime metrics worker:

      AttributeError: module 'ddtrace.internal.runtime' has no attribute 'runtime_metrics'

- Fixes an issue with enabling the runtime worker introduced in v0.49.0 where no runtime metrics were sent to the agent.

---

## v0.49.3

### Bug Fixes

- django: fix a bug where multiple database backends would not be instrumented.
- django: fix a bug when postgres query is composable sql object.

---

## v0.49.2

### Bug Fixes

- Fix double patching of `pymongo` client topology.

---

## v0.49.1

### New Features

- Add support for Flask 2

---

## v0.49.0

### Prelude

Several deprecations have been made to `Context` as we prepare to move active span management out of this class.

### Upgrade Notes

- Support for aiohttp previous to 2.0 has been removed.

- Support for deprecated <span class="title-ref">DD_PROFILING_API_URL</span> environment variable has been removed. Use <span class="title-ref">DD_SITE</span> instead.

- Support for deprecated <span class="title-ref">DD_PROFILING_API_KEY</span> environment variable has been removed. Use <span class="title-ref">DD_API_KEY</span> instead.

- Profiling support for agentless mode must now be explicitly enabled.

- The ddtrace pytest plugin can now label spans from test cases marked xfail with the tag "pytest.result"
  and the reason for being marked xfail under the tag "pytest.xfail.reason".

- Removed <span class="title-ref">ddtrace.ext.AppTypes</span> and its usages in the tracer library.

- requests: spans will no longer inherit the service name from the parent.

- The return value of `Span.pprint()` has been changed to a single line in the tracer debug logs rather than the previous custom multiline format.

- Spans are now processed per tracer instance. Formerly spans were stored per-Context which could be shared between tracer instances. Note that context management is not affected. Tracers will still share active spans.

- Spans from asynchronous executions (asyncio, gevent, tornado) will now be processed and flushed together. Formerly the spans were handled per-task.

- `tracer.write()` will no longer have filters applied to the spans passed to it.

- The function `ddtrace.utils.merge_dicts` has been removed.

### Deprecation Notes

- `Context.clone` is deprecated. It will not be required in 0.50.

- `Context.add_span` is deprecated and will be removed in 0.50.

- `Context.add_span` is deprecated and will be removed in 0.50.

- `Context.close_span` is deprecated and will be removed in 0.50.

- `Context.get_current_span` is deprecated and will be removed in 0.50 please use <span class="title-ref">Tracer.current_span</span> instead.

- `Context.get_current_root_span` is deprecated and will be removed in 0.50 please use `Tracer.current_root_span` instead.

- Deprecate the configuration of the analytics through the generic dbapi2 configuration. This should now be configured via integration configurations, for example:

      # Before
      export DD_TRACE_DBAPI2_ANALYTICS_ENABLED=1

      # After
      export DD_TRACE_SQLITE3_ANALYTICS_ENABLED=1

- <span class="title-ref">ddtrace.compat</span> has been deprecated and will be removed from the public API in ddtrace version 1.0.0.

- Deprecate <span class="title-ref">ddtrace.config.dbapi2</span> as default for <span class="title-ref">TracedCursor</span> and <span class="title-ref">TracedConnection</span> as well as <span class="title-ref">DD_DBAPI2_TRACE_FETCH_METHODS</span>. Use <span class="title-ref">IntegrationConfig</span> and <span class="title-ref">DD\_\<INTEGRATION\>\_TRACE_FETCH_METHODS</span> specific to each dbapi-compliant library. For example:

      # Before
      config.dbapi2.trace_fetch_methods = True

      # After
      config.psycopg2.trace_fetch_methods = True

- The use of `ddtrace.encoding` has been deprecated and will be removed in version 1.0.0.

- The <span class="title-ref">ddtrace.http</span> module has been deprecated and will be removed in version 1.0.0, with the <span class="title-ref">ddtrace.http.headers</span> module now merged into <span class="title-ref">ddtrace.trace_utils</span>.

- The `collect_metrics` argument of the `tracer.configure` method has been deprecated. Runtime metrics should be enabled only via the `DD_RUNTIME_METRICS_ENABLED` environment variable.

### New Features

- The futures integration is now enabled by default.
- requests: add global config support. This enables the requests service name to be configured with `ddtrace.config.requests['service']` or the `DD_REQUESTS_SERVICE` environment variable.

### Bug Fixes

- Fix broken builds for Python 2.7 on windows where `<stdint.h>` was not available. This change also ensures we build and publish `cp27-win` wheels.
- CGroup file parsing was fixed to correctly parse container ID with preceding characters.
- grpc: handle None values for span tags.
- grpc: handle no package in fully qualified method
- grpc: Add done callback in streaming response to avoid unfinished spans if a <span class="title-ref">StopIteration</span> is never raised, as is found in the Google Cloud libraries.
- grpc: handle IPv6 addresses and no port in target.
- Fix DD_LOGS_INJECTION incompatibility when using a `logging.StrFormatStyle` (`logging.Formatter(fmt, style="{")`) log formatter.
- Fixed a bug that prevented the right integration name to be used when trying to patch a module on import that is already loaded.
- Fix `urllib3` patching not properly activating the integration.
- gRPC client spans are now marked as measured by default.
- Fixes issue of unfinished spans when response is not a <span class="title-ref">grpc.Future</span> but has the same interface, as is the case with the base future class in <span class="title-ref">google-api-core</span>.
- In certain circumstances, the profiles generated in a uWSGI application could have been empty. This is now fixed and the profiler records correctly the generated events.
- The default agent timeout for profiling has been restored from 2 to 10 seconds to avoid too many profiles from being dropped.
- Fix issue with missing traces when using `pymemcache.client.hash.HashClient`.
- Added missing pymongo integration configuration, which allows overriding the service name for all the emitted spans.

### Other Changes

- Added environment variable <span class="title-ref">DD_BOTTLE_DISTRIBUTED_TRACING</span> to enable distributed tracing for bottle.
- The <span class="title-ref">attrs</span> library has been unvendored and is now required as a normal Python dependency with a minimum version requirement of 19.2.0.
- The <span class="title-ref">six</span> library has been removed from <span class="title-ref">vendor</span> and the system-wide version is being used. It requires version 1.12.0 or later.
- Documentation on how to use Gunicorn with the `gevent` worker class has been added.
- Added environment variable <span class="title-ref">DD_FALCON_DISTRIBUTED_TRACING</span> to enable distributed tracing for falcon.
- When extracting context information from HTTP headers, a new context is created when the trace ID is either 0 or not available within the headers.
- Added environment variable <span class="title-ref">DD_PYLONS_DISTRIBUTED_TRACING</span> to enable distributed tracing for pylons.
- Update `pymemcache` test suite to test latest versions.
- Added <span class="title-ref">config.pyramid.distributed_tracing</span> setting to integration config for pyramid.
- The `ddtrace.payload` submodule has been removed.
- Added environment variable <span class="title-ref">DD_TORNADO_DISTRIBUTED_TRACING</span> to enable distributed tracing for tornado.

---

## v0.48.5

### Bug Fixes

- django: fix a bug where multiple database backends would not be instrumented.
- django: fix a bug when postgres query is composable sql object.

---

## v0.48.4

### Bug Fixes

- grpc: handle None values for span tags.
- grpc: Add done callback in streaming response to avoid unfinished spans if a <span class="title-ref">StopIteration</span> is never raised, as is found in the Google Cloud libraries.

## v0.48.3

---

### Bug Fixes

- grpc: handle no package in fully qualified method
- grpc: handle IPv6 addresses and no port in target.
- gRPC client spans are now marked as measured by default.
- Fixes issue of unfinished spans when response is not a <span class="title-ref">grpc.Future</span> but has the same interface, as is the case with the base future class in <span class="title-ref">google-api-core</span>.

## v0.48.2

---

### Bug Fixes

- The default agent timeout for profiling has been restored from 2 to 10 seconds to avoid too many profiles from being dropped.

---

## v0.48.1

### Bug Fixes

- Fix `urllib3` patching not properly activating the integration.
- In certain circumstances, the profiles generated in a uWSGI application could have been empty. This is now fixed and the profiler records correctly the generated events.

---

## v0.48.0

### Upgrade Notes

- The deprecated <span class="title-ref">dogstatsd_host</span> and <span class="title-ref">dogstatsd_port</span> arguments to <span class="title-ref">tracer.configure()</span> have been removed.
- Support for gevent 1.0 has been removed and gevent \>= 1.1 is required.
- flask: deprecated configuration option <span class="title-ref">extra_error_codes</span> has been removed.
- The deprecated `pyddprofile` wrapper has been removed. Use `ddtrace-run` with `DD_PROFILING_ENABLED=1` set instead.
- A <span class="title-ref">ValueError</span> will now be raised on tracer initialization if the Agent URL specified to the initializer or with the environment variable <span class="title-ref">DD_TRACE_AGENT_URL</span> is malformed.
- **uWSGI** is no longer supported with `ddtrace-run` due to a limitation of how tracer initialization occurs. See the updated instructions for enabling tracing in the library `uWSGI documentation<uwsgi>`.

### New Features

- dogpile.cache: is now automatically instrumented by default.
- pylons: now supports all the standard http tagging including query string, custom error codes, and request/response headers.
- The ddtrace pytest plugin can now call `ddtrace.patch_all` via the `--ddtrace-patch-all` option.
- `Span` now accepts a `on_finish` argument used for specifying functions to call when a span finishes.
- Adds support for the Datadog Lambda Extension. The tracer will send traces to the extension by default if it is present.
- Add support for space-separated <span class="title-ref">DD_TAGS</span>.
- urllib3: add urllib3 integration

### Bug Fixes

- The `Records` parameter to `Firehose` endpoint calls is being excluded from the tags to avoid generating traces with a large payload.
- Tracer: fix configuring tracer with dogstatsd url.
- elasticsearch: patch versioned elasticsearch modules (elasticsearch1, ..., elasticsearch7).
- botocore: Do not assume that ResponseMeta exists in the results.
- django: handle erroneous middleware gracefully.
- The tracer now captures the task ID from the cgroups file for Fargate \>= 1.4.0 and reports it to the agent as the Datadog-Container-ID tag.
- Fix a bug when tracing a mako `DefTemplate` or any `Template` that does not have a `filename` property.
- flask: fix a bug when the query string would contain non-Unicode characters
- Fix a formatting issue on error exporting profiles.
- A workaround is provided for the problem with uWSGI worker processes failing to respawn. This can occur when using `ddtrace-run` for automatic instrumentation and configuration or manual instrumentation and configuration without the necessary uWSGI options. The problem is caused by how the tracer can end up starting threads in the master process before uWSGI forks to initialize the workers processes. To avoid this, we have provided updated instructions for enabling tracing in the library `uWSGI documentation<uwsgi>`.

### Other Changes

- The logic behind the header extraction for distributed tracing has been improved.
- The default connection timeout for the profiling agent has now been reduced from 10 to 2 seconds to match the tracer behavior.
- The tracemalloc memory profiler, which was disabled by default, has been removed.
- Query strings are stripped out from URLs by default when setting URL metadata on a span. This change affects all integrations that store HTTP metadata, like aiohttp, falcon, requests, urllib3.

---

## v0.47.0

### Upgrade Notes

- elasticsearch: removed <span class="title-ref">get_traced_transport</span> method and <span class="title-ref">ddtrace.contrib.elasticsearch.transport</span> module.
- The profiler now automatically sets up uWSGI compatibility in auto mode or with <span class="title-ref">profile_children=True</span>. Make sure that you don't have custom code instrumenting the profiler in those cases.
- The `Tracer` class properties DEFAULT_HOSTNAME, DEFAULT_PORT, DEFAULT_DOGSTATSD_PORT, DEFAULT_DOGSTATSD_URL, DEFAULT_AGENT_URL have been removed.

### New Features

- cherrypy: introduce TraceMiddleware for the CherryPy web framework.
- django: tag root spans as measured.
- elasticsearch: add support for version 7.
- fastapi: add integration.
- Introduce support for the DD_SERVICE_MAPPING environment variable to allow remapping service names on emitted spans.
- httplib: distributed tracing is now enabled by default.
- The profiler now supports most operation mode from uWSGI without much configuration. It will automatically plug itself in post fork hooks when multiprocess mode is used.
- wsgi: add tracing middleware.

### Bug Fixes

- Resolves an issue in Django tracing where, if <span class="title-ref">query_string</span> is not present in request.META, a KeyError is raised, causing the request to 500
- Deprecate the DD_LOGGING_RATE_LIMIT variable in favor of the standard DD_TRACE_LOGGING_RATE for configuring the logging rate limit.
- sampler: removed bug causing sample_rate of 0 to be reset to 1, and raise ValueError instead of logging.
- starlette: unpatch calls correctly.
- flask: fix memory leak of sampled out traces.
- Fix CPU time and wall time profiling not ignoring the profiler tasks with gevent.

### Other Changes

- The default maximum CPU time used for the stack profiler (CPU time, wall time and exceptions profiling) has been decreased from 2% to 1%.

---

## v0.46.0

### Upgrade Notes

- The profiler will only load tags from the DD_TAGS environment variable once at start.

### Deprecation Notes

- flask: Use `HTTP Custom Error Codes <http-custom-error>` instead of `ddtrace.config.flask['extra_error_codes']`.

### New Features

- aiohttp: store request and response headers.
- bottle: store request and response headers.
- flask: store response headers.
- molten: store request headers.
- pyramid: store request and response headers.
- flask: store request and response headers when auto-instrumented.
- The <span class="title-ref">ddtrace.profiling.auto</span> module will warn users if gevent monkey patching is done after the profiler is auto-instrumented.
- The <span class="title-ref">Profiler</span> object can now be passed tags with the <span class="title-ref">tags</span> keyword argument.

### Bug Fixes

- dbapi: avoid type error with potential non-compliance in db libraries when setting tag for row count.
- django: add legacy resource format of <span class="title-ref">{handler}</span>.
- grpc: fix wrapper for streaming response to support libraries that call an internal method directly.
- sanic: use path parameter names instead of parameter values for the resource.
- The profiler won't deadlock on fork when gevent monkey patch is enabled.
- requests: fix TracedSession when patches are not applied.

---

## v0.45.0

### Prelude

Build and deploy Python 3.9 wheels for releases

### Upgrade Notes

- Context.get() has been removed and the functionality has been rolled into Context.close_span().
- Tracer.record() has similarly been removed as it is no longer useful with Context.get() removed.
- The deprecated compatibility module <span class="title-ref">ddtrace.profile</span> has been removed.
- The profiler now uses the tracer configuration is no configuration is provided.

### Deprecation Notes

- The <span class="title-ref">pyddprofile</span> wrapper is deprecated. Use <span class="title-ref">DD_PROFILING_ENABLED=true ddtrace-run</span> instead.
- The profiler does not catch uncaught exception anymore.

### New Features

- botocore: added <span class="title-ref">distributed_tracing</span> configuration setting which is enabled by default.

- The ddtrace-run command now supports the following arguments:
  -h, --help -d, --debug enable debug mode (disabled by default) -i, --info print library info useful for debugging -p, --profiling enable profiling (disabled by default) -v, --version show program's version number and exit

  It now also has friendlier error messages when used incorrectly.

- Add functionality to call gevent.monkey.patch_all() with ddtrace-run by setting the environment variable DD_GEVENT_PATCH_ALL=true. This ensures that gevent patching is done as early as possible in the application.

- botocore: inject distributed tracing data to <span class="title-ref">ClientContext</span> to trace lambda invocations.

- botocore: inject tracing data to <span class="title-ref">MessageAttributes</span>.

- The profiler now tracks the running gevent Greenlet and store it as part of the CPU and wall time profiling information.

- The profiler is now able to upload profiles to the Datadog Agent by using a Unix Domain Socket.

- It is now possible to pass a <span class="title-ref">url</span> parameter to the <span class="title-ref">Profiler</span> to specify the Datadog agent location.

- The new memory profiler for Python is now enabled by default. This improves the profiler memory consumption and performance impact. It can still be disabled by setting <span class="title-ref">DD_PROFILING_MEMALLOC=0</span> as an environment variable.

- The profiler now uses the tracer configuration is no configuration is provided.

- pytest integration. This enables the [pytest](https://pytest.org) runner to trace test executions.

### Bug Fixes

- core: always reset the current_span in the context.
- django: Http404 exceptions will no longer be flagged as errors
- django: add safe guards for building http.url span tag.
- aiobotocore: set span error for 5xx status codes.
- elasticsearch: set span error for 5xx status codes.
- django, DRF, ASGI: fix span type for web request spans.
- Fixes span id tagging in lock profiling.
- Fix UDS upload for profiling not using the correct path.
- Fixed an issue in profiling exporting profiles twice when forking.
- core: fix race condition in TracerTagCollector.

### Other Changes

- Start-up logs are now disabled by default. To enable start-up logs use <span class="title-ref">DD_TRACE_STARTUP_LOGS=true</span> or <span class="title-ref">DD_TRACE_DEBUG=true</span>.

---

## v0.44.1

### Bug Fixes

- Fixes span id tagging in lock profiling.

---

## v0.44.0

### Prelude

Add support for Python 3.9

### New Features

- Store request headers in Flask integration.
- pyodbc integration. This enables the [pyodbc](https://github.com/mkleehammer/pyodbc) library to trace queries.
- starlette integration resource aggregation This aggregates endpoints to the starlette application resource that was accessed. It occurs by default but it is configurable through config.starlette\["aggregate_resources"\].
- The profiler now captures the traces information with the lock profiling.
- The Profiler instances now restart automatically in child process when the main program is forked. This only works for Python ≥ 3.7.

### Bug Fixes

- dbapi: add support for connection context manager usage
- django: check view before instrumenting MRO.
- core: use loose types when encoding.
- Patch pynamodb on import to prevent patching conflicts with gevent.
- tornado: handle when the current span is None in log_exception().

---

## 0.43.0 (5/10/2020)

- fix(django): avoid mixing str and non-str args for uri helper
- fix(asgi): tag 500-level responses as errors
- fix(asgi): set http status when exception raised
- fix(rediscluster): support rediscluster==2.1.0
- fix(asyncio): enable patch by default
- fix(asyncio): patch base event loop class
- fix(vertica): use strings in `__all__`
- feat(core): backport contextvars
- fix(sanic): fix patching for sanic async http server (#1659)
- fix(flask): make template patching idempotent
- fix(core): Do not rate limit log lines when in debug
- fix(profiling): Fix a potential deadlock on profiler restart after fork()

---

## 0.42.0 (14/09/2020)

- feat(django): add database_service_name config option
- feat: add global service name configuration for dbapi integrations
- fix(falcon): set span error for 5xx responses
- fix(core): always store span_type as str on span
- feat(pymongo): trace tcp connections
- fix(logging): cast span_id and trace_id as string when adding to the record.
- fix(gevent): patch ssl modules on import
- feat(core): add trace_utils module
- fix(core): expose http setting on global config
- feat(core): consolidate fork checks

---

## 0.41.2 (25/08/2020)

- Fix for an issue introduced by patching classes in the MRO of a Django View class (#1625).

---

## 0.41.1 (25/08/2020)

- reintroduce wrapt for patching Django view methods. ([#1622](https://github.com/DataDog/dd-trace-py/pull/1622))

---

## 0.41 (05/08/2020)

### Changes

- feat: add asgi integration (#1567)
- feat(profiling): reduce the default amount of sampling for memory/lock to 2% (#1586)
- fix(profiling/line2def): handle empty filename (#1585)
- fix(grpc): GRPC Channel Pin (#1582 -- thanks @munagekar)
- feat(core): make environment variables consistent with other languages (#1575)
- fix(profiling,gevent): fix race condition with periodic thread (#1569)
- fix(core): disable import hooks (#1563)
- feat(core): set tags from DD_TAGS (#1561)
- fix(profiling): lock Recorder on reset (#1560)
- feat(django): add option for using legacy resource format (#1551 -- thanks @tredzko, @jheld)
- feat(core): add startup logging (#1548)
- feat(core): add msgpack encoder (#1491)

### Full changeset

https://github.com/DataDog/dd-trace-py/compare/v0.40.0...v0.41.0


---


## 0.40.2 (25/08/2020)

- Fix for an issue introduced by patching classes in the MRO of a Django View class (#1625).

---


## 0.40.1 (25/08/2020)

- reintroduce wrapt for patching Django view methods. ([#1622](https://github.com/DataDog/dd-trace-py/pull/1622))

---

## 0.40 (17/07/2020)

# Upgrading to 0.40.0

This release includes performance improvements in the core library, updates profiling, and configuration changes to several integrations. This release also adds support for installing on Windows.

## grpc

* Use `ddtrace.config.grpc["service_name"]` to set service name reported by default for gRPC client instances. The ``DD_GRPC_SERVICE`` environment variable can also be used.

## redis

* Use `ddtrace.config.redis["service"]` to set the service name for the `redis` integration. The environment variable `DD_REDIS_SERVICE` can also be used.

## httplib

* To enable distributed tracing, use `ddtrace.config.httplib["distributed_tracing"]`. By default, distributed tracing for `httplib` is disabled.

# Changes

## Improvements

- fix(monkey): use lock on manual patched module add (#1479 -- thanks @uniq10)
- core: re-enable custom rng (#1474)
- core: rewrite queue (#1534)
- pin: make service optional (#1529)
- feat(ddtrace-run): allow to enable profiling (#1537)
- feat(profiling): enable flush on exit by default for Profiler (#1524)
- fix(grpc): compute service name dynamically (#1530)
- feat(redis): add global service configuration (#1515)
- feat(httplib): support distributed tracing (#1522 -- thanks @jirikuncar)
- refactor(profiling): hide Profiler._schedulers (#1523)
- feat(profiling): set Datadog-Container-Id when uploading profiles (#1520)
- feat(profiling/http): emit log message when server returns 400 (#1477)
- feat(profiling): validate API key format (#1459)

## Bug fixes

- fix(profiling): fix memory leak in Python 2 stack collector (#1568)
- fix(profiling): disable exception profiling on Windows (#1538)
- fix(profiling/stack): fix GIL switching (#1475)

## Full changeset

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.39.0...v0.40.0).

---

## 0.39.2 (25/08/2020)

- Fix for an issue introduced by patching classes in the MRO of a Django View class (#1625).

---

## 0.39.1 (25/08/2020)

- reintroduce wrapt for patching Django view methods. ([#1622](https://github.com/DataDog/dd-trace-py/pull/1622))

---

## 0.39 (11/06/2020)

### Deprecations

- The `decode()` method on our encoders located in `ddtrace.encoders` is scheduled to be removed in 0.40.

### Integrations

#### Django

- Add headers tracing configuration. This will enable headers on requests to be attached as tags on request spans. This can be done by using the `ddtrace.config` API like so: `config.django.http.trace_headers(["my-header-i-want-to-trace"])`. Thanks to @JoseKilo for contributing this feature!
- Fix for a bug where we didn't handle all cases for a template name. Thanks @sibsibsib!

#### Elasticsearch

- Fix for a bug that causes the tracer to crash if params is `None`.

#### grpc

- Fix handling `None` RpcMethodHandlers

### Tracer

- core: deprecate encoder decode()

### Profiling

- Now included by default with the tracing package
- Support for the `DD_ENV` and `DD_VERSION` environment variable configuration.
- A bunch of gevent fixes. See below for the details.

### OpenTracing

- Support for tracer interleaving between the Datadog tracer (used in integrations) and the OpenTracing tracer provided in the library.


## Full Change set

### Deprecations

- core: deprecate encoder decode() (#1496)

### Features

- docs: add changelog from github (#1503)
- feat(profiling): disable trace tracking in tracer by default (#1488)
- feat(profiling): allow to pass version to Profiler (#1478)
- feat(profiling): allow to pass env to Profiler (#1473)
- feat(profiling): install the profiling extra by default (#1463)
- feat(profiling): raise an error on start if endpoint is empty (#1460)
- feat(profiling): validate API key format (#1459)
- feat(profiling): track gevent greenlets (#1456)
- feat(profiling/pprof): export trace id as stack labels (#1454)
- feat(django): Implement headers tracing (#1443 -- thanks @JoseKilo)
- feat(profiling): allow to pass service_name to the Profiler object (#1440)
- feat(opentracing): support for tracer interleaving (#1394)

### Fixes

- fix(profiling): multi-threading/gevent issues with ThreadLinkSpan (#1485)
- refactor(profiling/recorder): remove filtering mechanism (#1482)
- fix(profiling/stack): lock _WeakSet in ThreadSpanLinks (#1469)
- span: changed finished attribute implementation (#1467)
- fix(grpc): RpcMethodHandler can be None (#1465)
- fix(elasticsearch): ensure params is a dict before urlencoding (#1449, #1451)
- fix(profiling): identify Python main thread properly with gevent (#1445)
- fix(django) handle different template view name types (#1441 -- thanks @sibsibsib)
- fix(profiling/periodic): make sure that a service cannot be started twice (#1439)
- fix(profiling): use gevent.monkey rather than private _threading module (#1438)
- fix(profiling): fix negative CPU time (#1437)
- fix(profiling/periodic): PERIODIC_THREAD_IDS race condition (#1435)

### Tests

- test: show 10 slowest tests (#1504)
- test(profiling): fix stress test (#1500)
- test(profiling): fix test_truncate random failure (#1498)
- test(profiling): fix test_collect_once random failure (#1497)
- test(profiling/http): fix fail cases (#1493)
- test(profiling): enable verbose + no capture (#1490)
- test(profiling): increase error tolerance (#1489)
- test(profiling): make stack stress test gevent compatible (#1486)
- tests: wait for uds server to start before using it (#1472)
- ci: update Python versions (#1446)

https://github.com/DataDog/dd-trace-py/compare/v0.38.2...v0.39.0

https://github.com/DataDog/dd-trace-py/milestone/57?closed=1

---


## 0.38.4 (25/08/2020)

- Fix for an issue introduced by patching classes in the MRO of a Django View class (#1625).

---

## 0.38.3 (25/08/2020)

- reintroduce wrapt for patching Django view methods. ([#1622](https://github.com/DataDog/dd-trace-py/pull/1622))

---

## 0.38.2 (11/06/2020)
This patch release disables span linkage by default in the profiler which was causing some threading lock up issues in gevent. See #1488 and #1485 for the details.

---

## 0.38.1 (28/05/2020)
# 0.38.1

This patch release includes 2 fixes:

- Fix span/trace id generation collisions when forking by reverting the changes we made to speed up id generation. (#1470)

- Force rebuilding of Cython files in setup.py. This will ensure that we build for the right Python
version. (#1468)
---

## 0.38.0 (21/05/2020)
# Upgrading to 0.38.0

## Integrations

### Celery
- Support for distributed tracing was added in #1194. It is default disabled but is easily enabled via `DD_CELERY_DISTRIBUTED_TRACING=true` or via the config API `ddtrace.config.celery['distributed_tracing'] = True`. Thanks @thieman!
- Analyzed span configuration was added to easily configure Celery spans to be analyzed. This can be done via the environment variable `DD_CELERY_ANALYTICS_ENABLED=true` or with the config API `ddtrace.config.celery['analytics_enabled'] = True`.

### Django
- A config option was added to allow enabling/disabling instrumenting middleware. It can be set with the environment variable `DD_DJANGO_INSTRUMENT_MIDDLEWARE=true|false` or via the config API `ddtrace.config.django['instrument_middleware'] = False # default is True`.


## Core
- Runtime ID tag support for traces and profiles. This will allow us to correlate traces and profiles generated by applications.

### OpenTracing
- Support for `active_span()` was added to our OpenTracing tracer.

### Profiling
- Storing of span id in profiling events which will enable linking between a trace and a profile in the product.

### Tracer
- `DD_TAGS` environment variable added to replace `DD_TRACE_GLOBAL_TAGS`.


# Changes

## New features
- opentracer: implement active_span (#1395)
- django: add config for instrumenting middleware (#1384)
- celery: Add analyzed span configuration option (#1383)
- Add runtime id tag support for traces & profiles (#1379)
- profiling: retry on upload error (#1376)
- tracer: Add DD_TAGS environment variable support (#1315)
- celery: Add distributed tracing (#1194 -- thanks @thieman)
- profiling: Store span ids in profiling events (#1043)


## Improvements
- monkey: add better error messages (#1430)
- Make hooks settings generic (#1428)
- internal: Change trace too large message to debug (#1403 -- thanks @ZStriker19)
- internal: Ensure queue is at or over max size before dropping (#1399)
- performance: improve span id generation (#1378)


## Bug fixes
- hooks: use log.error to log hook exceptions (#1436)
- writer: raise RuntimeError("threads can only be started once") (#1425 -- thanks @YasuoSasaki)
- settings: pass the `memodict` argument to subcall to deepcopy (#1401)
- profiling: correct the scheduler sleep time based on exporter time (#1386)
- integration config: copy and deepcopy implementations (#1381)
- utils: do not expose deepmerge function (#1375)


## Documentation
- Updated to pypi advance usage docs (#1402)


## Testing
- update tests to latest versions of libraries (#1434, #1433, #1432, #1424, #1423, #1422, #1421, #1420, #1419, #1418, #1417, #1416, #1412, #1411, #1410, #1409, #1408, #1407, #1406, #1405)
- circleci: remove unused busybox (#1426)

---

## 0.37.3 (25/08/2020)

- Fix for an issue introduced by patching classes in the MRO of a Django View class (#1625).

---

## 0.37.2 (25/08/2020)

- reintroduce wrapt for patching Django view methods. ([#1622](https://github.com/DataDog/dd-trace-py/pull/1622))


---

## 0.37.1 (15/05/2020)
# 0.37.1

This patch release includes fixes for install problems with Alpine Linux as well as for a bug with tag encoding in the profiler.

## Fixes

- fix(profiling/http): make sure tags are UTF-8 encoded (#1393)
- fix(profiling): allow to build on Alpine Linux (#1391)
- fix: don't allow package version to be `None` (#1380)
---

## 0.37.0 (27/04/2020)
# Upgrading to 0.37.0

This release introduces mainly bug fixes as well as some new configuration options for the profiling library.

## Profiler
New environment variables have been added to allow you to easily configure and describe your application in Datadog.

- `DD_SITE`: Specify which site to use for uploading profiles. Set to ``datadoghq.eu`` to use EU site.
- `DD_API_KEY`:  an alias to `DD_PROFILING_API_KEY`.
- `DD_ENV`:  the environment in which your application is running. eg: prod, staging
- `DD_VERSION`: the version of your application. eg: 1.2.3, 6c44da20, 2020.02.13
- `DD_SERVICE`: the service which your application represents.


# Changes

## Bug fixes
- tracer: stop previous writer if a new one is created (#1356)
- Fix task context management for asyncio in Python <3.7 (#1353)
- fix(profiling): pthread_t is defined as unsigned long, not int (#1347)
- span: handle non-string tag keys (#1345)
- fix(profiling): use formats.asbool to convert bool from env (#1342)
- fix: avoid context deadlock with logs injection enabled (#1338 -- thanks @zhammer)
- fix: make C extensions mandatory (#1333)
- fix(profiling): allow to override options after load (#1332)
- fix(profiling): ignore failure on shutdown (#1327)
- logging: fix docs typo (#1323)

## Integrations

- Add config to omit `django.user.name` tag from request root span (#1361 -- thanks @sebcoetzee)
- Update error event handler name within SQLAlchemy engine (#1324 -- thanks @RobertTownley)
- docs(opentracing): add celery example (#1329)


## Core

- refactor(tracer): remove property for Tracer.context_provider (#1371)
- writer: allow configuration of queue maxsize via env var (#1364)
- feat(profiling): replace http_client by urllib for uploading (#1359)
- feat(profiling): allow to pass service_name to HTTP exporter (#1358)
- Updates configuration docs (#1360 -- thanks @sburns)
- feat(profiling): add the pip install when recompilation is needed (#1334)
- tracer: support tracing across `fork()` (#1331)
- Allow Profiler to finish upload data in the background when stopped (#1322)


## Tests
- test(profiling): check for thread presence rather than number of threads (#1357)
- aiobotocore: pin to <1.0 (#1330)
- ci: allow to use latest pytest version (#1326)
- fix(tests/profiling): use a string with setenv, not an int (#1321)

---

## 0.36.3 (25/08/2020)

- Fix for an issue introduced by patching classes in the MRO of a Django View class (#1625).

---

## 0.36.2 (25/08/2020)

- reintroduce wrapt for patching Django view methods. ([#1622](https://github.com/DataDog/dd-trace-py/pull/1622))

---

## 0.36.1 (08/04/2020)
## Changes

This patch release addresses an issue when debug logging is enabled:

* fix: avoid context deadlock with logs injection enabled (#1338 -- thanks @zhammer)

### Changeset

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.36.0...v0.36.1).
---

## 0.36.0 (01/04/2020)
# Upgrading to 0.36.0

This release includes support for setting global application tags which will help Datadog better correlate your Python application traces and logs with other services in your infrastructure.

By providing the following settings, `ddtrace` will tag your application's traces and logs appropriately:

- `DD_ENV` environment variable or `ddtrace.config.env`: the environment in which your application is running. eg: `prod`, `staging`.
- `DD_VERSION` environment variable or `ddtrace.config.version`: the version of your application. eg: `1.2.3`, `6c44da20`, `2020.02.13`
- `DD_SERVICE` environment variable or `ddtrace.config.service`: the service which your application represents.

In you are using our logging integration manually, please update your formatter to also include the `dd.env`, `dd.service` and `dd.version` attributes as well. See our docs on [Logs Injection](http://pypi.datadoghq.com/trace/docs/advanced_usage.html?highlight=injection#logs-injection) for more details.

## Profiling
If you are using the profiler, please note that `ddtrace.profile` has been renamed to `ddtrace.profiling`.

# Changes

## Core

- core: Add support for DD_ENV (#1240)
- core: Add support for DD_VERSION (#1222)
- core: Add support for DD_SERVICE (#1280, #1292, #1294, #1296, #1297)
- inject dd.service, dd.env, and dd.version into logs (#1270)
- Log exporter support (#1276)
- chore: update wrapt to `1.12.1` (#1283)
- Update _dd.measured tag support (#1302)
- feat(tracer): deprecate global excepthook (#1307)


## Integrations

- guard against missing botocore response metadata (#1264 -- thanks @zhammer)
- integrations: prioritize DD_SERVICE (#1298)


## Profiler
- refactor: rename ddtrace.profile to ddtrace.profiling (#1289)
- fix(profiling): fix Lock issue when stored in a class attribute (#1301)
- feat(profiling): expose the Profiler object directly in ddtrace.profiling (#1303)
- fix(tests/profiling): use a string with setenv, not a int (#1321)
- fix(profiling/http): converts nanoseconds timestamp to seconds (#1325)

## Opentracing

- Add uds_path to ddtrace.opentracer.Tracer (#1275 -- thanks @worldwise001)


## Documentation
- fix import for profiling docs (#1271)
- fix profiler example and more details about API usage (#1284)
- Fixed typos in ddtrace.contrib.django docs (#1286 -- thanks @sergeykolosov)
- Fix tiny typo in Issue template (#1288)
- Typo in readme (#1300 -- thanks @Holek)


## Testing and tooling
- Check profiler accuracy (#1260)
- chore(ci): fix flake8 and pin pytest and readme_renderer (#1278)
- fix(tests,profile): do not test the number of locking events (#1282)
- fix: do not build wheels on Python 3.4 + run test buildin wheels in the CI (#1287)
- fix(tests,profiling): correct number of frames handling (#1290)
- fix(tests, opentracer): flaky threading test (#1293)
- build: use latest manylinux images (#1305)
- feat(wheels): update to manylinux2010 (#1308)

---

## 0.35.2 (25/08/2020)

- Fix for an issue introduced by patching classes in the MRO of a Django View class (#1625).

---

## 0.35.1 (25/08/2020)

- reintroduce wrapt for patching Django view methods. ([#1622](https://github.com/DataDog/dd-trace-py/pull/1622))

---

## 0.35.0 (13/03/2020)
## Upgrading to 0.35.0

This release adds:
- A Python profiler
- New hook registry for patching on import
- `botocore` support for `RequestId`
- Support `aiobotocore>=0.11.0`
- Support `rediscluster==2.0.0`
- Documentation for uWSGI
- Python 3.8 support

## Changes

### Core

* internal: Add import patching and import hook registry (#981)
* core: Add logs injection setting to Config (#1258)

### Integrations

* fix(aiobotocore): add support for aiobotocore>=0.11.0 (#1268)
* botocore - support tag for AWS RequestId (#1248 -- thanks @someboredkiddo)
* rediscluster: Add support for v2.0.0 (#1225)
* Use DATADOG_SERVICE_NAME as default tornado name (#1257 -- thanks @zhammer)

### Profiler

* Import profiling library (#1203)

### Documentation

* Django is automatically instrumented now (#1269 -- thanks @awiddersheim)
* docs: basic uWSGI section (#1251 -- thanks @MikeTarkington)
* fix doc for migrate from ddtrace<=0.33.0 (#1236 -- thanks @martbln)

### Testing and tooling

* ci: use medium resource class (#1266)
* fix(ci): use machine executor for deploy jobs (#1261, #1265)
* ci: improve tooling for building and pushing wheels (#1256, #1254, #1253, #1252)
* fix: local docs (#1250)
* chore(ci): use system Python version for flake8 and black target #1245)
* ci(tox): leverage tox factor to simplify setenv directives (#1244)
* fix(ci): remove virtualenv<20 limitation (#1242)
* refactor: use tox flavor to specify Celery usedevelop setting (#1238)
* ci: make sure we install cython for deploy_dev (#1232)
* ci: requires virtualenv < 20 (#1228)
* fix(tests): do not use numeric value for errno (#1226)
* fix(docs): s3 deploy requires rebuild of docs (#1223)
* fix(tests): use encoder decode in tests (#1221)
* fix(encoding): always return bytes when decoding (#1220)
* fix(tox): do not skip install for docs (#1218)
* add Python 3.8 support (#1098)

### Changeset

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.34.1...v0.35.0).
---

## 0.34.2 (25/08/2020)

- Fix for an issue introduced by patching classes in the MRO of a Django View class (#1625).

---

## 0.34.1 (09/03/2020)
## Changes

This patch release addresses issues with the new Django integration introduced in 0.34.0:

* fix(django): handle disallowed hosts (#1235)
* fix(django): patch staticmethods in views (#1246)
* docs(django): enabling automatically and manually (#1249)

### Changeset

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.34.0...v0.34.1).
---

## 0.34.0 (21/02/2020)
## Upgrading to 0.34.0

This release adds a new integration for Django. The goal of this effort was to make the Django integration more consistent with our other integrations, simplifying configuration and patching. See the [Django documentation](https://ddtrace.readthedocs.io/en/latest/integrations.html#django) for details on how to get started with the integration. For existing Django applications, be sure to consult the [migration section](https://ddtrace.readthedocs.io/en/v0.50.3/integrations.html#migration-from-ddtrace-0-33-0) of the documentation.

While we are now vendoring `psutil`, `msgpack` will no longer be vendored and instead specified as a requirement.

Finally, improvements have been made to the testing and continuous integration.

## Changes

### Core

* Unvendor msgpack (#1199, #1216, #1202)
* Vendor psutil (#1160)
* Update get_env to support any number of parts (#1208)
* Set _dd.measured tag on integration spans (#1196)
* Start writer thread as late as possible (#1193)
* Refactor setup.py c-extension building (#1191)

### Integrations

* New Django Integration (#1161, #1197)

### Testing and tooling

* CircleCI updates (#1213, #1212, #1210, #1209)
* Tox updates (#1214, #1207, #1206, #1205, #1204, #1201, #1200)
* Black updates (#1190, #1188, #1187, #1185)
* Fix botocore tests on py3.4 (#1189)


### Changeset

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.33.0...v0.34.0).
---

## 0.33.0 (23/01/2020)
## Upgrading to 0.33.0

This release introduces setting numeric tags as metrics, addresses a regression in Python 3 performance, and introduces a dual license.

## Changes

### Core

* Prefer random.getrandbits on Python 3+, fall back to urandom implementation on 2 (#1183 -- thanks @thieman)
* Set numeric tags on `Span.metrics` instead of `Span.meta` (#1169, #1182)
* Update default sampler to new `DatadogSampler` (#1172, #1166)
* Safely deprecate `ext` type constants (#1165)

### Tooling

* Fix botocore tests (#1177)

### Documentation

* Add Dual License (#1181)
* Improve `tracer.trace` docs (#1180 -- thanks @adamchainz)
---

## 0.32.1 (09/01/2020)
## Changes

This patch release addresses an issue with installation:

* use environment markers for install requirements (#1174  -- thanks @JBKahn, #1175)

### Changeset

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.32.0...v0.32.1).
---

## 0.32.2 (10/01/2020)
## Changes

This patch release addresses an issue with installation:

* add funcsigs backport to install (#1176)

### Changeset

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.32.1...v0.32.2).
---

## 0.32.0 (08/01/2020)
## Upgrading to 0.32.0

This release adds support for dogpile.cache, fixes an issue with the bottle integration, addresses issues with CI, and makes several improvements to the core library.

## Changes

### Core

- Deprecating app type (#1144, #1162)
- Refactor span types (#1150)
- Use debtcollector (#1152, #1147)
- Change DatadogSampler defaults (#1151)
- Initial black formatting (#1137, #1141)
- Health metrics improvements (#1135, #1134, #1131)

### Integrations

- Add support for dogpile.cache (#1123 -- thanks @goodspark)

### Bug fixes

- Bottle: fix status code for error responses (#1158)

### Tooling

- Improve flake8 checks (#1132)
- Pin multidict dependency for aiobotocore02 tests (#1145 -- thanks @codeboten)
- Fixing sqlalchemy test failures (#1138 -- thanks @codeboten)
- Remove unneeded tox dependencies (#1124)
---

## 0.31.0 (15/11/2019)
## Upgrading to 0.31.0

This release addresses issues with the gRPC, Celery, Elasticsearch integrations. In addition, there are internal improvements to how timing for spans is being handled.

## Changes

### Integrations

- celery: use strongrefs for celery signals (#1122) fixes #1011
- elasticsearch: Add support for elasticsearch6 module (#1089)
- grpc: improve handling exceptions (#1117, #1119) and use callbacks to avoid waits (#1097)
- opentracing: fix for compatibility tags (#1096 -- thanks @marshallbrekka)

### Core and Internal

- core: replace time.time by monotonic clock (#1109)
- core: rewrite agent writer on new process (#1106)
- core: add support for dogstatsd unix socket (#1101)
- core: always set rate limit metric (#1060)
- internal: fix setting analytics sample rate of None (#1120)
- internal: initial work on tracer health metrics (#1130, #1129, #1127, #1125)
- internal: use args for LogRecord when logging (#1116 -- thanks @karolinepauls)
- span: use ns time (#1113, #1112, #1105, #964)
- tracer: allow to override agent URL with a env var (#1054)

### Documentation

- docs: add a GitHub issue template (#1118)
- Remove extra import from tracer get_call_context code snippet (#1041)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.30.2...v0.31.0) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/47?closed=1).
---

## 0.30.2 (21/10/2019)
## Changes

This bug fix release introduces a fix for the agent writer that would cause traces to not be successfully flushed when the process forks.

Whenever we detect that we are in a new process (`os.getpid()` changes) we create a new queue to push finished traces into for flushing. In the previous version of the tracer (`v0.29.x`) we would also recreate the background thread that would read from this queue to ensure that we 1) had one background worker per-process and 2) had an updated reference to the new queue we created.

In the process of simplifying the code for the agent writer in `v0.30.0` we kept the code to recreate the queue, but didn't fully replicate the behavior for recreating the background thread. This meant that whenever the process was forked the background thread would maintain a reference to the old queue object and therefore be unable to pull traces from the queue we were pushing them into causing traces to get stuck in memory in the new queue (since no one was pulling from it).

With these changes we are recreating the entire background worker whenever the process is forked. This will cause us to once again create a new queue and new background thread for flushing finished traces.

### Core and Internal

* core: rewrite agent writer on new process (#1106)
* grpc: use callbacks to avoid waits (#1097)

### Changeset

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.30.1...v0.30.2).
---

## 0.30.1 (11/10/2019)
## Changes

### Core and Internal

* writer: disable `excepthook` metric by default (#1095)
* writer: send statistics less often and disable by default (#1094)

### Changeset

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.30.0...v0.30.1) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/48?closed=1).
---

## 0.30.0 (11/10/2019)
## Upgrading to 0.30.0

In addition to resolving several integration related issues, this release improves critical core components for tracing and runtime metrics.

## Changes

### Core and Internal

* core: ensure we cast sample rate to a float (#1072)
* core: add https support (#1055 -- thanks @raylu)
* core: tag sampling decision (#1045)
* internal: allow to ignore certain field when computing object size (#1087)
* internal: enable `rediscluster` by default (#1084)
* internal: remove unused slot (#1081)
* internal: fix iteration on slot class if attribute is unset (#1080)
* internal: add platform tags as default for runtime metrics (#1078)
* internal: add rate limit effective sample rate (#1046)
* runtime: add lang and tracer_version tags to runtime metrics (#1069)
* runtime: flush writer stats to dogstatsd (#1068)
* runtime: fix gc0 test (#1067)
* runtime: batch statsd flushes (#1063)
* runtime: add tracer env tag to runtime metrics (#1051)
* tracer: fix configure(collect_metrics) argument (#1066)
* tracer: count the number of unhandled exception via dogstatsd (#1077)
* tracer: grab the env tag from the tags, not a special var (#1070)
* tracer: expose finished attribute (#1058)
* tracer: remove tracer property function (#1042)
* writer: add memory size statistics to the queue (#1071)
* writer: add statistics to `Q` (#1065)

### Integrations

* aiohttp: handle 5XX responses as errors (#1082)
* bottle: handle 5XX responses as errors  (#1083)
* cassandra: handle batched bound statements in python3 (#1062 -- thanks @jdost)
* consul: add instrumentation for consul (#1048 -- thanks @phil-dd)
* consul: use consistent span name (#1053 -- thanks @phil-dd)
* grpc: fix channel interceptors (#1050)
* httplib: make docs consistent with implementation (#1049)
* tornado: code snippet fix in documentation (#1047)

### Changeset

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.29.0...v0.30.0) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/46?closed=1).
---

## 0.29.0 (05/09/2019)
## Upgrading to 0.29.0

This release introduces a new contextvars-based context manager in Python 3.7 and adds support for Tornado 5 and 6 with Python 3.7. In addition, the release includes several updates for our Django integration.

## Changes

### Core

[internal] Add generic rate limiter (#1029)
[internal] Add support for contextvars to tracer in py37 (#990)
[internal] Vendor monotonic package (#1026)
[dev] Update span test utils (#1028)
[dev] Allow extra args to scripts/run-tox-scenario (#1027)
[dev] Remove unused circle env vars for release (#1016)

### Integrations

[tornado] minor documentation fix (#1038)
[tornado] document overriding on_finish and log_exception (#1037)
[tornado] Add support for Tornado 5 and 6 with Python 3.7 (#1034)
[pymongo] Add support for PyMongo 3.9 (#1023)
[django] enable distributed tracing by default (#1031)
[django] Create test for empty middleware (#1022 -- thanks @ryanwilsonperkin)
[django] Patch DBs in django app config (#1019 -- thanks @JBKahn)
[django] Setup pytest-django (#995)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.28.0...v0.29.0) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/45?closed=1).
---

## 0.28.0 (16/08/2019)
## Upgrading to 0.28.0

This release introduces container tagging and adds support for gRPC server.

## Changes

### Integrations

* [grpc] Add support for GRPC server (#960)
* [django] Only set sample rate if rate is set (#1009)
* [django] Update how we get the http.url (#1010)
* [django] Improve Django docs (#1002 -- thanks @adamchainz)
* [pylibmc] Fix client when tracer is disabled (#1004)

### Core

* [core] Parse and send container id with payloads to the agent (#1007)
* [internal] Change log from exception to debug (#1013)
* documentation bugfix (#1017)
* Add back release:wheel (#1015)
* [tests] Adding in helpful default packages (#1008)
* [dev] Map .git into ddtest for setuptools_scm (#1006)
* Use setuptools_scm to handle version numbers (#999)
* Use Python 3 for test_build job (#994)
* writer: fix deprecated log.warn use (#993 -- thanks @deterralba)
* Upload wheels on release (#989)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.27.1...v0.28.0) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/44?closed=1).
---

## 0.27.1 (25/07/2019)
# Upgrading to 0.27.1

This patch release includes performance fix which is highly recommended for anyone currently using 0.27.0.

# Changes

* 0.27 Performance Fix #1000

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.27.0...v0.27.1)
---

## 0.27.0 (12/07/2019)
## Upgrading to 0.27.0

This release introduces improvements to the trace writer. In addition, the release fixes some issues with integrations.

## Changes

### Core

* api: implement __str__ (#980)
* API: add Unix Domain Socket connection support (#975)
* [core] Remove references to runtime-id (#971)
* sampler: rewrite RateByServiceSampler without using Lock (#959)
* Handle HTTP timeout in API writer (#955)
* payload: raise PayloadFull on full payload (#941)

### Integrations

* [pymongo] Support newer msg requests (#985)
* pymongo: Add missing 2013 opcode (#961)
* Refs #983 - Make AIOTracedCursor an async generator  (#984 -- thanks @ewjoachim)
* Fix a typo in AIOTracedCursor docstring (#982 -- thanks @ewjoachim)
* [sqlalchemy] Only set sample rate if configured (#978)

### Documentation

* LICENSE: Fix copyright holder notice (#977 -- thanks @underyx)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.26.0...v0.27.0) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/43?closed=1).
---

## 0.26.0 (05/06/2019)
## Upgrading to 0.26.0

This release introduces several core improvements and continues addressing pain points in our tooling and testing.

## Changes

### Core

* Add a PeriodicWorker base class for periodic tasks (#934)
* Fix runtime workers not flushing to Dogstatsd (#939)
* api: simplify _put codepath (#956)
* make psutil requirement more accurate (#949 -- thanks @chrono)
* writer: log a message when a trace is dropped (#942)
* span: use system random source to generate span id (#940)
* [core] Add config to set hostname tag on trace root span (#938)

### Integrations

* Support keyword 'target' parameter when wrapping GRPC channels (#946 -- thanks @asnr)
* Record HTTP status code correctly when using abort() with Bottle (#943 -- thanks @equake)
* boto: add support for Python 3.5+ (#930)

### Documentation

* fix documentation for current_root_span (#950 -- thanks @chrono)

### Tooling

* Run flake8 with Python 3 (#957)
* tox: fix ignore path for integrations (#954)
* Remove mention of -dev branch in CircleCI (#931)

### Testing

* [tests] Add benchmarks (#952)
* [tests] increase deviation for sampler by service test (#948)
* [tests] Fix thread synchronization (#947)
* [tests] fix brittle deviation test for tracer (#945)
* [tests] fix threading synchronization to opentracer test (#944
* [pyramid] Fix dotted name for autopatched config test (#932)
* tests: always skip sdist, use develop mode (#928)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.25.0...v0.26.0) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/42?closed=1).
---

## 0.25.0 (07/05/2019)
## Upgrading to 0.25.0

This release includes several core improvements and addresses pain points in our testing/CI. The release also adds a new integration for Algolia Search.

## Changes

### Improvements

- Type cast port number to avoid surprise unicode type (#892 -- thanks @tancnle)
- Add support for Python 3.7 (#864)
- [writer] Enhance Q implementation with a wait based one (#862)
- [core] Add Span 'manual.keep' and 'manual.drop' tag support (#849)
- [core] Vendor msgpack dependency (#848)
- Fix http.url tag inconsistency (#899)
- aiohttp: do not set query string in http.url tag (#923)
- tornado: do not include query string in the http.url tag (#922)
- bottle: fix query string embedded in URL (#921)
- django: remove query string from http.url tag (#920)

### Integrations

- Implement algolia search (#894)

### Tooling

- [dev/tooling] Enforce single quote strings (#884)
- [tests] Leverage tox environment listing to simplify CircleCI tox target list (#882)

### Testing

- tests/tornado: enhance `test_concurrent_requests` (#915)
- doc: disable fixed sidebar (#906)
- [opentracer] Refactor time usage (#902)
- [opentracer] Fix flaky test based on sleep (#901)
- [internal] Add and use RuntimeWorker.join() to remove race condition in testing (#887)
---

## 0.24.0 (15/04/2019)
## Upgrading to 0.24.0

This release introduces a new feature (disabled by default), supports new versions of integrations and improves our testing and tooling.

## Changes

### Improvements

- [core] Enable requests integration by default (#879)
- [core] Fix logging with unset DATADOG_PATCH_MODULES (#872)
- [core] Use DEBUG log level for RateSampler initialization (#861 -- thanks @bmurphey)
- [core] Guard against when there is no current call context (#852)
- [core] Collect run-time metrics (#819)

### Integrations

- [mysql] Remove mysql-connector 2.1 support (#866)
- [aiobotocore] Add support for versions up to 0.10.0 (#865)

### Tooling

- [dev/tooling] Update flake8 to 3.7 branch (#856)
- [dev/tooling] Add script to build wheels (#853)
- [ci] Use tox.ini checksum to update cache (#850)

### Testing

- [tests] Use a macro to persist result to workspace in CircleCI (#880)
- [tests] add psycopg2 2.8 support (#878)
- [aiohttp] Fix race condition in testing (#877)
- [docs] Remove confusing testing instructions from README (#874)
- [tests] Add support for aiohttp up to 3.5 (#873)
- Remove useless __future__ imports (#871)
- [testing] Remove nose usage (#870)
- [tests] Add support for pytest4 (#869)
- [tests] Add testing for Celery 4.3 (#868)
- [tests] Enable integration tests in docker-compose environment (#863)
- [tests] Do not test celery 4.2 with Kombu 4.4 (#858)
- [tests] Fix ddtrace sitecustomize negative test (#857)
- [tests] Use spotify cassandra image for tests (#855)
- [tests] Fix requests gevent tests (#854)
---

## 0.23.0 (19/03/2019)
## Upgrading to 0.23.0

With this release we are introducing a new configuration system across integrations to generate APM events for [Trace Search & Analytics](https://docs.datadoghq.com/tracing/visualization/search/). The other core changes are the beginnings of a new approach to address issues with tracer loads and improve debugging.

## Changes

### Improvements

* Trace search client configuration (#828)
* [core] fix wrapt wrappers sources (#836)
* [core] Add Payload class helper (#834)
* [internal] Add rate limited logger (#822)

### Bugs

* Fix for broken celery tests (#839 -- thanks @JackWink)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.22.0...v0.23.0) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/39?closed=1).
---

## 0.22.0 (01/03/2019)
## Upgrading to 0.22.0

This release contains a few improvements for not marking a Celery task as an error if it is an expected and allowed exception, for propagating synthetics origin header, and to vendor our `six` and `wrapt` dependencies.

## Changes
### Improvements
- [celery] Don't mark expected failures as errors (#820 -- thanks @sciyoshi)
- [core] Propagate x-datadog-origin (#821)
- [core] vendor wrapt and six dependencies (#755)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.21.1...v0.22.0) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/36?closed=1).

---

## 0.21.1 (21/02/2019)
## Upgrading to 0.21.1

This is a bug fix release that requires no changes to your code.

Included in this release is a fix for some database cursors where we would force `Cursor.execute` and `Cursor.executemany` to return a cursor instead of the originally intended output. This caused an issue specifically with MySQL libraries which tried to return the row count and we were returning a cursor instead.

## Changes
### Bugs
* [core] Patch logging earlier for ddtrace-run (#832)
* [dbapi2] Fix dbapi2 execute/executemany return value (#830 )
* [core] Use case-insensitive comparison of header names during extract (#826 -- thanks @defanator)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.21.0...v0.21.1) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/38?closed=1).

---

## 0.21.0 (19/02/2019)
## Upgrading to 0.21.0

With this release we are moving distributed tracing settings to be enabled by default. This change means that you no longer need to explicitly enable distributed tracing for any integration.

## Changes
### Improvements
- Enable distributed tracing by default (#818)
  - aiohttp
  - bottle
  - flask
  - molten
  - pylons
  - pyramid
  - requests
  - tornado
- [testing] Ensure consistent use of override_config and override_env (#815)
- [core] Break up ddtrace.settings into sub-modules (#814)
- [tests] Simplify elasticsearch CI test commands (#813)
- [core] Remove sending of service info (#811)
- [core] Add import hook module (#769)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.20.4...v0.21) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/35?closed=1).
---

## 0.20.4 (08/02/2019)
## Upgrading to 0.20.4

This is a bug fix release, no code changes are required.

In this release we have fixed a bug that caused some configuration values to not get updated when set.

## Changes
### Bug fixes
* [bug] Integration config keys not being updated (#816)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.20.3...v0.20.4) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/37?closed=1).

---

## 0.20.3 (04/02/2019)
## Upgrading to 0.20.3

This is a bug fix release that requires no changes.

This release includes a fix for context propagation with `futures`. Under the right conditions we could incorrectly share a trace context between multiple `futures` threads which result in multiple traces being joined together in one.

## Changes
### Bug fixes
* [core] Allow futures to skip creating new context if one doesn't exist (#806)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.20.2...v0.20.3) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/37?closed=1).
---

## 0.20.2 (29/01/2019)
## Upgrading to 0.20.2

No changes are needed to upgrade to `0.20.2`.

This big fix release includes changes to ensure we properly read the HTTP response body from the trace agent before we close the HTTP connection.

## Changes
### Bug fixes

- [core] Call HTTPResponse.read() before HTTPConnection.close() (#800)

### Improvements
- [tests] limit grpcio version to >=1.8.0,<1.18.0 (#802)
- [tools] Add confirmation to 'rake pypi:release' task (#791 )

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.20.1...v0.20.2) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/36?closed=1).
---

## 0.20.1 (17/01/2019)
## Upgrading to 0.20.1

No changes are needed to upgrade

## Changes
### Bug fixes
[celery] Ensure `celery.run` span is closed when task is retried (#787)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.20.0...v0.20.1) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/36?closed=1).
---

## 0.20.0 (09/01/2019)
# Upgrading to 0.20.0

We have added support for logs injection to the tracer. If you are already using `ddtrace-run`, the integration can be enabled with setting the environment variable `DD_LOGS_INJECTION=true`. The default behavior once logs injection is enabled is to have trace information inserted into all log entries. If you prefer more customization, you can manually instrument and configure a log formatter with the tracer information.

# Changes

## New Integrations

* [mako] Add Mako integration (#779 -- thanks @wklken)

## Enhancements

* [core] Tracer and logs integration (#777)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.19.0...v0.20.0) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/34?closed=1).
---

## 0.19.0 (28/12/2018)
## Upgrading to 0.19.0

With `0.19.0` we have decided to disable the tracing of `dbapi2` `fetchone()`/`fetchmany()`/`fetchall()` methods by default.

This change effects all integrations which rely on the `dbapi2` API, including `psycopg2`, `mysql`, `mysqldb`, `pymysql`, and `sqlite3`.

We have introduced this change to reduce the noise added to traces from having these methods (mostly `fetchone()`) traced by default.

With `fetchone()` enabled the traces received can get very large for large result sets, the resulting traces either become difficult to read or become too large causing issues when flushing to the trace agent, potentially causing traces to be dropped.

To re-enable the tracing of these methods you can either configure via the environment variable `DD_DBAPI2_TRACE_FETCH_METHODS=true` or manually via:

```python
from ddtrace import config
config.dbapi2.trace_fetch_methods = True
```

## Changes
### Bugs
[dbapi2] disable fetchone/fetchmany/fetchall tracing by default (#780)
[opentracing] Fixing context provider imports for scope manager (#771 -- thanks @Maximilien-R)

### Enhancements
[tests] test python setup.py sdist and twine check on build (#782)
[core] Add API to configure Trace Search (#781)
[core] Enable priority sampling by default (#774)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.18.0...v0.19.0) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/32?closed=1).
---

## 0.18.0 (12/12/2018)
## New Integrations

* [molten] Add molten support (#685)

## Bug Fixes

* [aws] Blacklist arguments stored as tags (#761)
* [psycopg2] Fix composable query tracing (#736)

## Improvements

* [aiohttp] Add HTTP method to the root span resource (#652 -- thanks @k4nar)
* [aws]Flatten span tag names (#768)
* [opentracer] Set global tags (#764)
* [core] add six and replace custom compat functions (#751)
* [config] make IntegrationConfig an AttrDict (#742)
* [tests] remove unused monkey.py test file (#760)
* [tests] fix linting in test files (#752)
* [psycopg2] fix linting issues (#749)
* [tests] have most tests use pytest test runner (#748)
* [tests] Provide default implementation of patch test methods (#747)
* [tests] run flake8 on all test files (#745)
* [tests] Add patch mixin and base test case (#721)
* [tests] Add Subprocess TestCase (#720)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.17.1...v0.18.0) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/30?closed=1).
---

## 0.17.1 (05/12/2018)
This release includes the removal of service sending, this should resolve many of the 400s that are being returned from the Agent resulting in an unfriendly `ERROR` message and giving the impression that the tracer is failing. (#757)

## Improvements
- [core] Make writing services a no-op (#735)
- [tests] upgrade flake8 to 3.5.0 (#743)
- remove flake8 ignores and fix issues (#744)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.17.0...v0.17.1) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/31?closed=1).
---

## 0.17.0 (28/11/2018)
## New features
- [redis] add support for redis 3.0.0 (#716)
- [core] Allow DD_AGENT_HOST and DD_TRACE_AGENT_PORT env variables (#708)
- [core] Add global tracer tags (#702)
- [core] Trace http headers (#647)

## Improvements
- [docs] add Flask configuration documentation (#734)
- Add long_description to setup.py (#728)
- [tests] pin version of redis-py-cluster for 'tox -e wait' (#725)
- [requests] Add another split_by_domain test (#713)
- [docs] Add kombu references (#711)
- [ci] Use small circleci resource class for all jobs (#710)
- [requests] patch Session.send instead of Session.request (#707)
- [ci] reorganize CircleCI workflows (#705)
- [elasticsearch] add support for elasticsearch{1,2,5} packages (#701)
- [tests] add base test case classes and rewrite tracer tests (#689)
- [dbapi] Trace db fetch and session methods (#664)

## Bugfixes
- [elasticsearch] add alias for default _perform_request (#737)
- [tests] Pin pytest to 3.x.x and redis to 2.10.x for rediscluster (#727)
- [django] Use a set instead of list for cache_backends to avoid duplicates (#726 -- thanks @wenbochang)
- [tests] fix broken redis check (#722)
- [docs] Fix broken flask link (#712)
- [mongodb] Fix pymongo query metadata (#706)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.16.0...v0.17.0) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/29?closed=1).
---

## 0.16.0 (13/11/2018)
## New Integrations
* [jinja2] Add jinja2 integration (#649 -- thanks @mgu)
* [kombu] add Kombu integration (#515 -- thanks @tebriel)
* [grpc] Add grpc client support. (#641)
* [gevent] Support gevent 1.3 (#663)
* [flask] rewrite Flask integration (#667)

## Bug Fixes
* [mysqldb] Fix mysqldb monkey patch (#623 -- thanks @benjamin-lim)
* [requests] exclude basic auth from service name (#646 -- thanks @snopoke)

## Improvements
* [core] Add IntegrationConfig helper class (#684)
* [core] add support for integration span hooks (#679)
* [httplib, requests] Sanitize urls in span metadata (#688)
* [tests] ensure we are running tests.contrib.test_utils (#678)
* [celery] [bottle] Add span type information for celery and bottle. (#636)
* [ci] Reorganize autopatch test calls (#670)
* [core] initial support for partial flushes (#668)
* [django] Remove query from django db span's tag sql.query (#659)
* [tests] Make CI faster by disabling dist and install in autopatching tests (#654)
* [core] Trace http headers (#647)
* [django] Infer span resource name when internal error handler is used (#645)
* [elasticsearch] Make constant organization consistent with other integrations (#628)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.15.0...v0.16.0) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/28?closed=1).

---

## 0.15.0 (16/10/2018)
**New integrations**

- Add [rediscluster](https://pypi.org/project/redis-py-cluster/) integration (#533, #637)
- Add [Vertica](https://github.com/vertica/vertica-python) Integration (#634)

**Bug fixes**

- [django] Fix minimum Django version for user.is_authenticated property (#626 -- thanks @browniebroke)

**Improvements**

- [celery] Add retry reason metadata to spans (#630)
- [core] Update config to allow configuration before patching (#650)
- [core] Add Tracer API to retrieve the root Span (#625)
- [core] Fixed `HTTPConnection` leaking (#542 -- thanks @mackeyja92)
- [django] Allow Django cache to be seen as a different service. (#629)
- [gevent] Patch modules on first import (#632)
- [gevent] Add support for gevent.pool.Pool and gevent.pool.Group (#600)
- [redis] Removed unused tag (#627)
- [requests] Patch modules on first import (#632)
- [tests] Add Span.span_type tests (#633)
- [tests] Update the integrations libraries versions to the latest possible. (#607)
- [tests] CircleCI run tests in the new alpine-based test runner (#638)
- [tests] Add test cases for API._put (#640)
- [tests] Skip flaky TestWorkers.test_worker_multiple_traces test case (#643)
- [tests] Remove tests for not supported gevent 1.3 (#644)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.14.1...v0.15.0) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/25?closed=1).
---

## 0.14.1 (25/09/2018)
**Bug fixes**
- [opentracer] Activate span context on extract (#606, #608)
- [opentracer] Fix "does not provide the extra opentracing" (#611, #616)

**Improvements**
- [docs] Clarify debug mode (#610)
- [docs] Fix docstring for `Tracer.set_tags` (#612 -- thanks @goodspark)
- [docs] Add priority sampling to ddtrace-run usage (#621)
- [circleci] Imrpve python docs deployment strategy (#615)
- [tests] Refactor tox.ini file (#609)
- [tests] Improve performance of tests execution (#605)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.14.0...v0.14.1) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/27?closed=1).
---

## 0.14.0 (11/09/2018)
**OpenTracing**

In this release we are happy to introduce the beta for the long-awaited OpenTracing compatible API layer for `ddtrace`!

Support for `opentracing>=2.0.0` is provided in this release. Namely, the following are supported:

- `start_span`/`start_active_span`
- `inject` and `extract` functionality
- `baggage`, through `set_baggage_item` and `get_baggage_item`
- compatible tags from the [OpenTracing specification](https://github.com/opentracing/specification/blob/b193756f1fe646b79ef4f901bed92c0e72845440/semantic_conventions.md#standard-span-tags-and-log-fields)
- scope manager support
- seamless integration with the Datadog tracer when using `ddtrace-run`

For setup information and usage see [our docs for the Datadog OpenTracing tracer](http://pypi.datadoghq.com/trace/docs/installation_quickstart.html#opentracing).


**CI Improvements**

Also included in this release are some optimizations to our CI which should get things running a bit quicker.

Thanks @labbati!



Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.13.1...v0.14.0) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/20?closed=1).
---

## 0.13.1 (04/09/2018)
**Bug fixes**

* [core] remove the root logger configuration within the library (#556)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.13.0...v0.13.1) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/24?closed=1).
---

## 0.13.0 (23/08/2018)
**New integrations**
- [`pymemcache`](https://github.com/pinterest/pymemcache) integration (#511)

**Celery integration**

Due to some limitations with our Celery integration, we changed our instrumentation to a [signals based approach](http://docs.celeryproject.org/en/latest/userguide/signals.html). We also started using import hooks to instrument Celery, so that enabling the instrumentation doesn't trigger a `celery` import.

- Signals implementation: #530
- Moving to import hooks: #534
- Resolved issues: #357, #493, #495, #495, #510, #370

**Breaking changes**
Using the signal based approach increase the stability of our instrumentation, but it limits what is currently traced. This is a list of changes that are considered breaking changes in the behavior and not in the API, so no changes are needed in your code unless you want a different behavior:
- By default all tasks will be traced if they use the Celery signals API, so tasks invoked with methods like `apply()`,  `apply_async()` and `delay()` will be traced but tasks invoked with `run()` will **not** be traced.
- `patch_task()` is deprecated; if it's used, all tasks will be instrumented

**Bug fixes**
- [core] check if bootstrap dir is in path before removal (#516 -- thanks @beezz!)
- [core] have hostname default to `DATADOG_TRACE_AGENT_HOSTNAME` environment variable if available (#509, #524 -- thanks @hfern!)
- [core] add WSGI-style http headers support to HTTP propagator (#456, #522)
- [core] Enable buffering on `getresponse` (#464, #527)
- [core] configure the root logger (#536)
- [aiopg] set the `app_type` during initialization (#492, #507)
- [boto] default to `None` if no region (#525, #526)
- [flask] avoid double instrumentation when `TraceMiddleware` is used (#538)
- [pymongo] fix multiple host kwargs (#535)
- [tornado] make settings object accessible during configuration (#499, #498 -- thanks @kave!)

**Improvements**
- [core/helpers] add a shortcut to retrieve Trace correlation identifiers (#488)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.12.1...v0.13.0) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/21?closed=1).
---

## 0.12.1 (14/06/2018)
**Bugfixes**

* [celery] add support for celery v1 tasks (old-style tasks) (#465, #423)
* [celery] `ddtrace-run` broke third-party script support; now it handles correctly the `argv` params (#469, #423)
* [celery] patch `TaskRegistry` to support old-style task with `ddtrace-run` (#484)
* [django] update error handling if another middleware has handled the exception already (#418, #462)
* [django] `DatabaseWrapper` loaded in right thread, after removing `setting_changed` signal from the `DatadogSettings` (#481, #435)
* [django/celery] add `shared_task` decorator wrapper to trace properly Celery tasks (#486, #451)
* [django/docs] notes about Debug Mode, and debugging (#476 -- thanks @ndkv!)
* [gevent] pass `sampling_priority` field when Distributed Tracing is enabled (#457)
* [mysqlb] add missing services info when they're flushed (#468, #428)
* [psycopg2] properly patch the driver when `quote_ident` typing is used (#477, #474, #383)
* [pylons] ensure the middleware code is Python 3 compatible to avoid crashes on import (#475, #472)
* [requests] add missing services info when they're flushed (#471, #428)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.12.0...v0.12.1) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/19?closed=1).
---

## 0.12.0 (03/05/2018)
**New integrations**
* [boto] Botocore and boto instrumentation is enabled by default using `patch_all()` (#319)
* [futures] provide context propagation for `concurrent` module (#429, [docs](http://pypi.datadoghq.com/trace/docs/#module-ddtrace.contrib.futures))
* [mysql] add `pymysql` support (#296, [docs](http://pypi.datadoghq.com/trace/docs/#mysql) -- thanks @wklken)

**Improvements**
* [core] introducing a low-level API to define configurations for each integration. This API is used only by the `requests` module and will be implemented in other integrations in newer releases (#445, #443, #450, #454, #441)
* [celery] split the service name in `celery-producer` and `celery-worker` for better stats (#432)
* [falcon] add distributed tracing (#437)
* [requests] provide a default service name for the request `Span` (#433)
* [requests] add `split_by_domain ` config to split service name by domain (#434)
* [tornado] better compatibility using `futures` instrumentation (#431)

**Bugfixes**
* [core] ensure `sitecustomize.py` is imported when `ddtrace-run` wrapper is used (#458)
* [flask] use `ddtrace` logger instead of Flask to avoid having a custom log filter (#447, #455)

**Breaking changes**
* [celery] the name of the service is now split in two different services: `celery-producer` and `celery-worker`. After the upgrade, you'll stop sending data to what was the default service name (`celery`). You should check the new services instead because you'll see a drop. Previously reported traces in the `celery` service, are still available if you move back the time selector.

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.11.1...v0.12.0) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/17?closed=1).
---

## 0.11.1 (30/03/2018)
**Improvements**

* [bottle] use the `route` argument in `TracePlugin`, to support Bottle 0.11.x (#439)

**Bugfixes**

* [django] gunicorn gevent worker wasn't instrumenting database connections (#442)
* [django] remove `MIDDLEWARE_CLASSES` deprecation warning from tests (#444)
* [django] ensure only `MIDDLEWARE` or `MIDDLEWARE_CLASSES` are loaded with tracing middlewares (#446)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.11.0...v0.11.1) and the [release milestone](https://github.com/DataDog/dd-trace-py/milestone/18?closed=1).
---

## 0.11.0 (05/03/2018)
**Security fixes**

* [dbapi] remove `sql.query` tag from SQL spans, so that the content is properly obfuscated in the Agent. This security fix is required to prevent wrong data collection of reported SQL queries. This issue impacts only MySQL integrations and NOT `psycopg2` or `sqlalchemy` while using the PostgreSQL driver. (#421)

**New integrations**

* [django] add support for Django 2.0 (#415 -- thanks @sciyoshi!)
* [mysql] `MySQL-python` and `mysqlclient` packages are currently supported (#376 -- thanks @yoichi!)
* [psycopg2] add support for version 2.4 (#424)
* [pylons] Pylons >= 0.9.6 is officially supported (#416)

**Bugfixes**

* [core] `ddtrace-run` script accepts `DATADOG_PRIORITY_SAMPLING` to enable [Priority Sampling](http://pypi.datadoghq.com/trace/docs/#priority-sampling) (#426)
* [pylons] add distributed tracing via kwarg and environment variable (#425, [docs](http://pypi.datadoghq.com/trace/docs/#module-ddtrace.contrib.pylons))
* [pylons] `ddtrace-run` script can patch a `PylonsApp` (#416)
* [pylons] add tracing to Pylons `render` function (#420)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.10.1...v0.11.0) and [0.11.0 milestone](https://github.com/DataDog/dd-trace-py/milestone/15?closed=1).
---

## 0.10.1 (05/02/2018)
**Distributed Tracing**
Add distributed tracing using integration settings for the following libraries/frameworks:
* `bottle` (#382)
* `requests` (#372)
* `pyramid` (#403)

**Improvements**
* [core] provide constants to pick Priority Sampling values (#391)
* [django] add support for Django Rest Framework (#389)
* [tooling] add missing classifiers for pypi (#395 -- thanks @PCManticore)
* [tornado] patch `concurrent.futures` if available, improving the way traces are built when propagation happens between threads (#362 -- thanks @codywilbourn)

**Bugfixes**
* [httplib] don't overwrite return value (#380 -- thanks @yoichi)
* [psycopg2] patch all imports of `register_type` (#393 -- thanks @stj)
* [pyramid] keep request as part of `render` kwargs (#384 -- thanks @joual)
* [pyramid] use pyramid `HTTPExceptions` as valid response types (#401, #386 -- thanks @TylerLubeck)
* [requests] add `unpatch` and double-patch protection (#404)
* [flask] don't override code of already handled errors (#390, #409)
* [flask] allow mutability of `resource` field within request (#353, #410)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.10.0...v0.10.1).
---

## 0.10.0 (08/11/2017)
**Distributed Sampling (beta)**

New feature that propagates the sampling priority across services. This is useful to mark traces as "don’t keep the trace" or "must have" when distributed tracing is used. This new functionality requires at least the Datadog Agent 5.19+. Frameworks with out-of-the-box support are: Django, Flask, Tornado (#358, #325, #359, #364, #366, #365, #371, [docs](http://pypi.datadoghq.com/trace/docs/#priority-sampling))

**Improvements**
* [core] update the Context propagation API, that includes a new way to retrieve and set the current active `Span` context. (#339)
* [core] implement Propagators API to simplify Distributed Tracing. You can use `HTTPPropagator` class to inject and extract the tracing context in HTTP headers (#363, #374 [docs](http://pypi.datadoghq.com/trace/docs/#ddtrace.propagation.http.HTTPPropagator))
* [celery] use service name from `DATADOG_SERVICE_NAME` env var, if defined (#347 -- thanks @miketheman)
* [django] respect env Agent host and port if defined (#354 -- thanks @spesnova)

**Bugfixes**
* [pylons] handle exception with non standard 'code' attribute (#350)
* [pyramid] the application was not traced when the tween list was explicitly specified (#349)

Read the full [changeset](https://github.com/DataDog/dd-trace-py/compare/v0.9.2...v0.10.0)
---

## 0.9.2 (12/09/2017)
**New features**
* [django] disable database or cache instrumentation via settings so that each Django component instrumentation can be disabled (#314, [docs](http://localhost:8000/#module-ddtrace.contrib.django) -- thanks @mcanaves)
* [django] it's not required anymore to add the Django middleware because the Django app ensures that it is installed. You can safely remove `ddtrace.contrib.django.TraceMiddleware` for your middleware list after the upgrade. This is not mandatory but suggested (#314, #346)
* [cassandra] trace `execute_async()` operations (#333)

**Bugfixes**
* [mysql]  prevent the Pin from attaching empty tags (#327)
* [django] fixed the initialization order to prevent logs when the tracer is disabled (#334)
* [sqlite3] add tests to ensure that services are properly sent (#337)
* [pyramid] fixed Pyramid crash when 'include()' is used with relative import paths (#342)
* [pylons] re-raise the exception with the original traceback in case of errors. Before Pylons exceptions were correctly handled but hidden by the tracing middleware. (#317)
* [pyramid] disable autocommit in Pyramid patching, to avoid altering the `Configurator` behavior (#343)
* [flask] fix Flask instrumentation that didn't close Jinja spans if an error was thrown (#344)

**Integration coverage**
* officially support ElasticSearch 1.6+ (#341)

**Documentation**
* fixed usage examples for `patch_all()` and `patch()` (#321 -- thanks @gomlgs)
* added a section about updating the hostname and port (#335)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.9.1...v0.9.2).
---

## 0.9.1 (01/08/2017)
**New features**
* [core] add a processing pipeline to the `AsyncWorker`, so that traces can be filtered easily. This change doesn't have any performance impact with existing installations, and is expected to work well with async frameworks / libraries (#303, [docs](http://pypi.datadoghq.com/trace/docs/#trace-filtering))
* [core] add language and library version metadata to keep track of them in the Datadog Agent. All values are sent via headers (#289)

**Bugfixes**
* [aiobotocore] update `async with` context manager so that it returns the wrapper instead of the wrapped object (#307)
* [boto, botocore] change the service metadata app for AWS with a more meaningful name (#315)

**Documentation**
* improving documentation so that it's more explicit how a framework should be auto-instrumented (#305, #308)
* add the list of auto-instrumented modules (#306)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.9.0...v0.9.1).
---

## 0.9.0 (05/07/2017)
**New features**

* [core] add process ID in root spans metadata (#293)

**Improvements**

* [falcon] extended support for Falcon 1.2; improved error handling (#295)
* [gevent] create a new `Context` when a Greenlet is created so that the tracing context is automatically propagated with the right parenting (#287)
* [asyncio] providing helpers and `patch()` method to automatically propagate the tracing context between different asyncio tasks (#260 #297, [docs](http://pypi.datadoghq.com/trace/docs/#module-ddtrace.contrib.asyncio) -- thanks @thehesiod)
* [aiohttp] add experimental feature to continue a trace from request headers (#259, [docs](http://pypi.datadoghq.com/trace/docs/#module-ddtrace.contrib.aiohttp) -- thanks @thehesiod)
* [django] add `DEFAULT_DATABASE_PREFIX` setting to append a prefix to database service (#291, [docs](http://pypi.datadoghq.com/trace/docs/#module-ddtrace.contrib.django) -- thanks @jairhenrique)

**Bugfixes**

* [logging] use specific logger instead of the root one in `monkey.py` module (#281)
* [django] `ddtrace` exception middleware catches exceptions even if a custom middleware returns a `Response` object (#278)
* [pylons] handle correctly the http status code when it's wrongly formatted (#284)
* [django] request resource handles the case where the `View` is a partial function (#292)
* [flask] attach stack trace to Flask errors (#302)

**New integrations**

* [httplib] add patching for `httplib` and `http.lib`(#137 -- thanks @brettlangdon)
* [aio-libs] add `aiobotocore` support (#257, #298, [docs](http://pypi.datadoghq.com/trace/docs/#module-ddtrace.contrib.aiobotocore) -- thanks @thehesiod)
* [aio-libs] add `aiopg` support (#258, [docs](http://pypi.datadoghq.com/trace/docs/#module-ddtrace.contrib.aiopg) -- thanks @thehesiod)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.8.5...v0.9.0).
---

## 0.8.5 (30/05/2017)
**Bugfixes**

* [flask] add the http method to flask spans (#274)
* [sqlite3] changed the app_type to `db` (#276)
* [core] `span.set_traceback()`now sets the traceback even if there's no exception (#277)

Read the [full changeset][1].

[1]: https://github.com/DataDog/dd-trace-py/compare/v0.8.4...v0.8.5
---

## 0.8.4 (19/05/2017)
**Bugfixes**

* [flask] avoid using weak references when Flask is instrumented via Blinker. This resolves initialization issues when the `traced_app = TraceMiddleware(app, ...)` reference goes out of the scope or is garbage collected (#273)
---

## 0.8.3 (15/05/2017)
**Improvements**

* [transport] add presampler header (`X-Datadog-Trace-Count`) so that the receiving agent has more information when dealing with sampling (#254)
* [docs] updated our documentation (#264, #271)

**Bugfixes**
* [core] patch loader raises `PatchException` that is handled in the `patch_all()` when the patch failed. This distinguishes: errors during patch, when an integration is not available and simply when the module is not installed (#262)
* [mysql] distinguish `MySQL-Python` instrumentation so that only `mysql-connector` package is patched; this provides better feedback about what library is supported (#263, #266)
* [sqlalchemy] provide a `patch()` method that uses the PIN object; this is not a breaking change, but the preferred way to instrument SQLAlchemy is through `patch_all(sqlalchemy=True)` or `patch(sqlalchemy=True)` (#261)
* [pylons] catch `BaseException` since a `SystemExit` might've been raised; `500` errors are handled if a timeout occurs (#267, #270)
* [pyramid] catch `BaseException` since a `SystemExit` might've been raised; `500` errors are handled if a timeout occurs (#269)

Read the [full changeset][1]

[1]: https://github.com/DataDog/dd-trace-py/compare/v0.8.2...v0.8.3
---

## 0.8.2 (28/04/2017)
**Bugfixes**

* [django] handle tuple `INSTALLED_APPS` for Django < 1.9 (#253)

Read the [full changeset][1]

[1]: https://github.com/DataDog/dd-trace-py/compare/v0.8.1...v0.8.2
---

## 0.8.1 (30/05/2017)
**Bugfixes**

* [core] fixed `msgpack-python` kwarg usage for versions earlier than `0.4.x` (#245)
* [pyramid] add request method to Pyramid trace span resource name (#249, thanks @johnpkennedy)

Read the [full changeset][1].

[1]: https://github.com/DataDog/dd-trace-py/compare/v0.8.0...v0.8.1
---

## 0.8.0 (10/04/2017)
**New integrations**
* Add support for Tornado web `4.0+`. Currently this integration is ignored by autopatching, but can be enabled via `patch_all(tornado=True)` (#204, [docs][1] -- thanks @ross for reviewing and testing the implementation)

**Bugfixes**
* [docs] Minor updates to our documentation (#239, #237, #242, #244 -- thanks @liubin @pahaz)
* [boto] Boto2 and Botocore integrations have safety check to prevent double patching (#240)
* [boto] Use frames directly without calling `getouterframes()`. This is a major improvement that reduces the impact of our tracing calls for Boto2 (#243 -- thanks @wackywendell)
* [django] make `func_name` work with any callable and not only with functions (#195, #203 -- thanks @m0n5t3r)

**Breaking change**
* [elasticsearch] when importing `elasticsearch` before executing `patch_all()`, no traces are created. This patch changed where the `PIN` object is attached, so you should update your instrumentation as described below (#238)

**Migrate from 0.7.x to 0.8.0**

* [elasticsearch] the PIN object was previously attached to the `elasticsearch` module while now it uses `elasticsearch.Transport`. If you were using the `Pin` to override some tracing settings, you must update your code from:
```python
Pin.override(client, service='elasticsearch-traces')
```
to:
```python
Pin.override(client.transport, service='elasticsearch-traces')
```

**Internals update**
* the Python traces logs and returns error when there is a communication issue with the APM Agent (#173)
* the `wrap()` tracer decorator can be extended by Python integrations when the usual approach is not suitable for the given execution context (#221)

Read the [full changeset][2].

[1]: http://pypi.datadoghq.com/trace/docs/#module-ddtrace.contrib.tornado
[2]: https://github.com/DataDog/dd-trace-py/compare/v0.7.0...v0.8.0
---

## 0.7.0 (29/03/2017)
**New integrations**
* Add support for `boto` (>=2.29.0) and `botocore` (>= 1.4.51) #209 . Currently these integrations are ignored by autopatching, but can be enabled via `patch_all(boto=True, botocore=True)`

**New features**
* Add the `ddtrace-run` command-line entrypoint to provide tracing without explicit additions to code. More information here http://pypi.datadoghq.com/trace/docs/#get-started #169

**Bugfixes**
* [dbapi] Ensure cursors play well with context managers #231
* [django] Provide a unique `datadog_django` app label to avoid clashes with existing app configs #235
* [pyramid] Ensure pyramid spans have method and route metadata consistent with other web frameworks #220 (thanks @johnpkennedy)
---

## 0.6.0 (09/03/2017)
**New integrations**
* Add support for asynchronous Python. This is a major improvement that adds support for `asyncio`, `aiohttp` and `gevent` (#161, docs: [asyncio][1] - [aiohttp][2] - [gevent][3])
* Add Celery integration (#135, #196, [docs][6])

**New features**
* Add explicit support for Python 3.5, and 3.6 (#215, see [supported versions][7])
* print the list of unfinished spans if the `debug_logging` is activated; useful in synchronous environments to detect unfinished/unreported traces (#210)

**Bugfixes**
* [mysql] `mysql` integration is patched when using `patch()` or `patch_all()` (#178)
* [django] set global tracer tags from Django `DATADOG_TRACE` setting (#159)
* [bottle] wrong `tracer` reference when `set_service_info` is invoked (#199)

**Breaking changes**
* Default port `7777` has been replaced with the new `8126` available from Datadog Agent 5.11.0 and above (#212)
* Removed the `ThreadLocalSpanBuffer`. It has been fully replaced by the `Context` propagation (#211)

**Migrate from 0.5.x to 0.6.0**

* Datadog Agent 5.11.0 or above is required.
* If you're using the `ThreadLocalSpanBuffer` manually, you need to use the [Context class][8] in your logic so that it is compliant with the `Context` propagation. Check the [Advanced usage][9] section.

**Advanced usage**
This is a list of new features that may be used for manual instrumentation when you're using a library or a framework that is not currently supported:
* Use `Context` propagation instead of a global buffer. This plays well with asynchronous programming where a context switching may happen while handling different logical execution flows (#172)
* `tracer.trace()` handles automatically the `Context` propagation and remains the preferable API
* Add `tracer.get_call_context()` to retrieve the current `Context` instance that is holding the entire trace for this logical execution ([docs][4])
* Add `start_span` as a way to manually create spans, while handling the Context propagation ([docs][5])

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.5.5...v0.6.0).

[1]: http://pypi.datadoghq.com/trace/docs/#module-ddtrace.contrib.asyncio
[2]: http://pypi.datadoghq.com/trace/docs/#module-ddtrace.contrib.aiohttp
[3]: http://pypi.datadoghq.com/trace/docs/#module-ddtrace.contrib.gevent
[4]: http://pypi.datadoghq.com/trace/docs/#ddtrace.Tracer.get_call_context
[5]: http://pypi.datadoghq.com/trace/docs/#ddtrace.Tracer.start_span
[6]: http://pypi.datadoghq.com/trace/docs/#module-ddtrace.contrib.celery
[7]: http://pypi.datadoghq.com/trace/docs/#supported-versions
[8]: https://github.com/DataDog/dd-trace-py/blob/853081c0f2707bcda59c50239505a5ceaed33945/ddtrace/context.py#L8
[9]: http://pypi.datadoghq.com/trace/docs/#advanced-usage
---

## 0.5.5 (15/02/2017)
**Improvements**
- ElasticSearch integration takes care of the returning status code in case of a `TransportError` (#175)

**Bugfixes**
- Pyramid integration handles properly the `Span.error` attribute if the response is a server error (#176)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.5.4...v0.5.5).

---

## 0.5.4 (14/02/2017)
## Integrations
- added the Pyramid web framework

## Enhancements
- `tracer.set_tags()` will add tags to all spans created by a tracer.
- `span.tracer()` will return the tracer that created a given span

## Bug Fixes
- correctly set service types on the Mongo and Falcon integrations.
- documentation fixes
- send less data to the agent in the SQL and redis integrations.

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.5.3...v0.5.4)

---

## 0.5.3 (23/12/2016)
**Bugfixes**
- [ElasticSearch] use ElasticSearch serializer so that the serialization works with dates, decimals and UUIDs #131
- [Django] use an integer value for `AGENT_PORT` because Django recast strings as unicode strings, which invalidate the input for `getaddrinfo()` in Python 2.7 #140
- [Tracer] downgrade high throughput log messages to debug so that it doesn't flood users logs #143

**Compatibility**
- don't check if `django.contrib.auth` is installed through the `django.apps` module. This improves the best-effort support for `Django < 1.7` #136

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.5.2...v0.5.3)

---

## 0.5.2 (14/12/2016)
0.5.2 is a bugfix release.

### Bug Fixes
- include bottle docs

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.5.1...v0.5.2).

---

## 0.5.1 (13/12/2016)
0.5.1 is a bugfix release.

### Bug Fixes
- properly trace pymongo `$in` queries (See #125)
- properly normalize bound and batch cassandra statements (see #126)
- made the legacy cassandra tracing a no-op.

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.5.0...v0.5.1).

---

## 0.5.0 (07/12/2016)
**Major changes**
- added`msgpack-python` as a dependency
- using Trace Agent API `v0.3` that supports both JSON and Msgpack formats
- provided `JSONEncoder` and `MsgpackEncoder` that are switched at runtime the API `v0.3` is not reachable (`404`)
- `MsgpackEncoder` is the current default encoder
- `MsgpackEncoder` will not be used if the pure Python implementation is used

**Documentation**
- added [ElasticSearch docs](http://pypi.datadoghq.com/trace/docs/#module-ddtrace.contrib.elasticsearch)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.4.0...v0.5.0)

---

## 0.4.0 (26/11/2016)
0.4.0 is a "major" release of the `dd-trace-py`. Please test thoroughly on staging before rolling out to your production clusters.

### Enhancements
- automatically patch contrib libraries with `from ddtrace import monkey; monkey.patch_all()`. A few notes:
  - The previous ways of patching still exist, but are deprecated and might be no-ops. They will be removed in a future version.
  - When you add `patch_all` remove your old instrumentation code.
  - Web frameworks still require middleware.
- experimental support for (much faster) msgpack serialization. disabled by default. will be enabled in a future release.

### Integrations
- add integration for the [Bottle](web framework) web framework. (see #86)

### Bug Fixes
- correctly trace django without auth middleware (see #116)

###

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.3.16...v0.4.0).

---

## v0.3.16 (03/11/2016)
### Bugfixes
- Handle memory leaks when tracing happens in a forked process (Issue #84)
- Fix error code in spans from the request library (thanks @brettlangdon)
- Better handling of unicode tags (thanks @brettlangdon)
- Allow easy configuration of host & port in the Django integration.

### Enhancements
- Cap the number of traces buffered in memory.
- Higher trace submission throughput.
- Preliminary work on gevent support. Not fully complete.

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.3.15...v0.3.16)

---

## v0.3.15 (01/11/2016)
### Integrations
- add tracing for the requests library

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.3.14...v0.3.15)

---

## 0.3.14 (30/09/2016)
### Integrations
- [pylons] allow users to set resources inside handlers
- [django] add support for the Django cache framework

### Enhancements
- add a trace sampler so that users can discard spans using a `RateSampler` (more info: http://pypi.datadoghq.com/trace/docs/#sampling)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.3.13...v0.3.14)

---

## v0.3.13 (21/09/2016)
### New integrations
- added `pylibmc` Memcached client integration
- improved Django integration providing a Django app that instrument Django internals

Read the full [changeset](https://github.com/DataDog/dd-trace-py/compare/v0.3.12...v0.3.13)

---

## v0.3.12 (14/09/2016)
[change set](https://github.com/DataDog/dd-trace-py/compare/v0.3.11...v0.3.12)
- Added MySQL integration, using [mysql.connector](https://dev.mysql.com/doc/connector-python/en/) v2.1

---

## v0.3.11 (31/08/2016)
## Bugfixes
- fixed an unpacking error for `elasticsearch>=2.4`
- fixed the behavior of the `tracer.wrap()` method; now it works as expected

## Documentation
- better organization of libraries usage on docs
- provided a [benchmark script](https://github.com/DataDog/dd-trace-py/commit/7d30c2d6703e21ea3dc94ecdeb88dbe2ad9a286a)

Read the [full changeset](https://github.com/DataDog/dd-trace-py/compare/v0.3.10...v0.3.11)

---

## v0.3.10 (22/08/2016)
[change set](https://github.com/DataDog/dd-trace-py/compare/v0.3.9...v0.3.10)
- add `flask_cache` integration; supporting the `0.12` and `0.13` versions
- catch `500` errors on `pylons` integration

---

## v0.3.9 (12/08/2016)
[change set](https://github.com/DataDog/dd-trace-py/compare/v0.3.8...v0.3.9)
- send service info from the sqlalchemy integration

---

## v0.3.7 (12/08/2016)
[change set](https://github.com/DataDog/dd-trace-py/compare/v0.3.6...v0.3.7)
- Released Falcon Integration
- Minor bugfixes in Redis & Cassandra integration

---

## v0.3.8 (12/08/2016)
[change set](https://github.com/DataDog/dd-trace-py/compare/v0.3.7...v0.3.8)
- Added support for the most recent bugfix versions of pymongo 3.0, 3.1 and 3.2
