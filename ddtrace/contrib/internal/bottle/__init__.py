"""Bottle request subscriber.

Bottle supplies WebFrameworkRequestEvent metadata around the matched route
callback. WebFrameworkRequestSubscriber owns propagation, span creation, HTTP
tags, and completion. Returned and raised HTTPResponse objects supply their
own status and headers; raised responses set response_from_exception so the
subscriber applies status-based OpenTelemetry error semantics.

The event channel remains web.request and the default tracing operation
remains bottle.request. Completion occurs when the synchronous callback
returns or raises, before response iteration and later Bottle hooks. Bottle
continues to own plugin ordering, skip settings, and callback-cache resets.
"""
