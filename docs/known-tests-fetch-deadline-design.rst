Known Tests Fetch Deadline Design
=================================

Status
------

This document describes the recommended evolution of known-tests fetching for
Test Optimization. The first-stage mitigation is implemented, while the hard
request deadline and shared cache remain follow-up work.

Context
-------

Known tests are fetched synchronously during Test Optimization session startup.
The endpoint is paginated and the client must retrieve every page before it can
safely classify a collected test as known or new. Session initialization waits
for that work even though it runs concurrently with test-management fetching and
git upload.

``DD_CIVISIBILITY_BACKEND_API_TIMEOUT_MILLIS`` configures the timeout passed to
``http.client.HTTPConnection``. That value is a socket inactivity timeout. It is
not a wall-clock limit for a complete request: a response that continues to
deliver bytes can remain active longer than the configured timeout. The same
timeout and generic five-attempt retry policy are used by all Test Optimization
endpoints.

Pagination multiplies this behavior. Each page can consume several attempts,
and the current page-count guard permits up to 10,000 pages. If any page fails,
the client discards all previously fetched pages, so repeated retries late in
the fetch can delay startup without producing a usable partial result.

Goals
-----

* Bound the time that known-tests fetching can add to session startup.
* Preserve successful operation for repositories whose known-test data requires
  multiple pages.
* Keep timeout and retry policy specific to known tests rather than making all
  Test Optimization requests more aggressive.
* Preserve correct known/new classification when pagination is incomplete.
* Avoid repeating the same large fetch for every test session in one CI job.
* Keep existing public API contracts unchanged.

Non-goals
---------

* Changing the backend pagination contract or cursor format.
* Returning partial known-tests data.
* Making known-tests fetching asynchronous with respect to test collection.
* Reducing the global backend socket inactivity timeout.

Stage 1: Immediate Mitigation
-----------------------------

The immediate mitigation introduces
``_DD_CIVISIBILITY_KNOWN_TESTS_TIMEOUT_MILLIS``. When it is unset, the budget is
the greater of 45 seconds and ``DD_CIVISIBILITY_BACKEND_API_TIMEOUT_MILLIS``.
An explicit known-tests timeout overrides that derived default. The client checks
the elapsed monotonic time before requesting each page after the first. When the
budget is exhausted, the client marks known-tests configuration as failed and
returns an empty set.

Known-tests page requests use two attempts instead of the connector's generic
five attempts. This is intentionally conservative: a failed page invalidates the
complete result, and five attempts can multiply an already expensive late-page
failure. The second attempt retains one opportunity to recover from a transient
failure. Other Test Optimization endpoints keep their existing retry policy.

The dd-trace-py riot configuration uses a 20-second pagination budget because
its jobs run several independent test sessions under a shared 20-minute job
timeout.

This stage is not a hard deadline. An individual active response can exceed the
budget because the budget is checked only between page requests. Limiting each
page to two attempts bounds retry amplification and ensures that the next page is
not started after the budget, but it cannot interrupt a slow streaming response.

Stage 2: Deadline-aware HTTP Requests
-------------------------------------

``BackendConnector`` should accept an optional absolute monotonic deadline, or
an equivalent total request budget, independently of its socket inactivity
timeout. The deadline must cover connection establishment, request upload,
response headers, response-body reads, retry delays, and every retry attempt.

The request loop should compute the remaining budget before each blocking phase.
It must not begin a retry when the budget is exhausted. Backoff sleeps must be
clamped to the remaining budget. Exhaustion should produce the existing timeout
error category so callers retain current error handling.

Response-body handling requires particular care. Setting a socket timeout once
does not create an absolute deadline because each successful read resets the
inactivity interval. The implementation should read incrementally while updating
the socket timeout from the remaining absolute deadline, or use another mechanism
that can reliably cancel an in-progress read. The design must work for plain,
gzip, HTTPS, and Unix-domain-socket responses.

Known-tests fetching should create one deadline for the entire paginated
operation and pass the remaining deadline through every page request. It should
not allocate a fresh full timeout to each page. The page-count limit remains a
defensive guard against invalid cursors, while the deadline becomes the primary
startup bound.

Endpoint-specific Retry Policy
------------------------------

The known-tests fetch should retain its two-attempt endpoint-specific retry
limit after hard deadlines are implemented. Both attempts must share the
original total deadline.

Retry configuration must not extend the total fetch budget. Retriable errors
remain network failures, inactivity timeouts, server errors, rate limits, and
invalid JSON. A rate-limit delay that exceeds the remaining budget should end the
fetch rather than block session startup.

All-or-nothing Results
----------------------

The client must continue returning an empty result when any page is missing or
invalid. Returning accumulated pages would make the set non-empty, causing every
test from an unfetched page to be classified as new. That can incorrectly enable
Early Flake Detection retries and increase runtime substantially.

An empty result deliberately disables known/new classification for the session.
This is preferable to classification based on incomplete data. Accumulated page
data should be released when the fetch fails and must not be written to a cache.

Job-local Cache
---------------

After hard deadlines are available, a short-lived cache can remove repeated
known-tests downloads across riot or other multi-session CI jobs. The cache
should be opt-in initially and use a directory already intended for ephemeral
Test Optimization data.

The cache key must include the backend site or agent URL, repository URL,
service, environment, and test configuration fields sent to the endpoint. Cache
entries should contain only a fully completed response and an expiry timestamp.
A short time-to-live scoped to a single CI job avoids carrying known-test state
across unrelated runs.

Writers should use a process lock and atomic rename so concurrent sessions cannot
observe a partial file. A process that loses the write race should read the
completed entry. Invalid, expired, or unreadable entries should be removed or
ignored and followed by a normal network fetch. Network failures must not replace
a valid entry with an empty result.

Because stale known-test data can classify a genuinely new test as known, cache
reuse should favor freshness: job scope, a short expiry, and complete cache keys
are required. The implementation should not use a stale entry after expiry as a
fallback unless product semantics explicitly approve that behavior.

Compatibility and Rollout
-------------------------

Both the current ``ddtrace.testing`` client and any still-supported legacy CI
Visibility client should converge on the same deadline and result semantics.
The connector API change should be additive, leaving existing callers on their
current timeout behavior until they opt into a deadline.

Rollout should proceed in three changes:

1. Ship the two-attempt page policy and between-page pagination budget.
2. Add connector-level hard deadlines and switch known tests to one deadline for
   the complete fetch.
3. Add the opt-in job-local cache, then evaluate enabling it by default for
   multi-session CI environments.

Verification
------------

Unit tests should cover valid and invalid budget configuration, budget exhaustion
between pages, no request after exhaustion, two attempts per page, and discarded
partial results. Deadline-aware connector tests should use a local server that
continues sending bytes frequently enough to avoid the inactivity timeout and
verify that the absolute deadline still terminates the request.

Additional tests should cover deadline exhaustion during retry backoff, gzip
responses, Unix-domain sockets, and cleanup of the connection after cancellation.
Cache tests should cover key separation, expiry, concurrent writers, atomic
publication, corrupt entries, and refusal to cache partial results.
