## Payload Model
- Trace Root Span - the first span in a trace
- Base Application Root Span => The first span for the current application
- Service Root Span => The first span within a service boundary (note that an application may have multiple services)

## Semantic Model
- Base Application Name - the name of the application.
- Service Name - the name of the service

## Thoughts
- We can use JSON schema to validate the shape of the payload and the fields
- Trace: A trace is a series of synchronous operations, starting with some external trigger
- Span: A span is a single operation within a trace
- Traces and spans represent a series of operations going through a set of systems

## Sample Trace
[[
  {
    "name": "pyramid.request",
    "service": "pyramid",
    "resource": "GET hello",
    "trace_id": 0,
    "span_id": 1,
    "parent_id": 0,
    "type": "web",
    "error": 0,
    "meta": {
      "_dd.p.dm": "-0",
      "_dd.p.tid": "654a694400000000",
      "component": "pyramid",
      "http.method": "GET",
      "http.route": "/",
      "http.status_code": "200",
      "http.url": "http://localhost:8000/",
      "http.useragent": "python-requests/2.28.1",
      "language": "python",
      "pyramid.route.name": "hello",
      "runtime-id": "af71bf0b951244fba40d444b10b2e462",
      "span.kind": "server"
    },
    "metrics": {
      "_dd.measured": 1,
      "_dd.top_level": 1,
      "_dd.tracer_kr": 1.0,
      "_sampling_priority_v1": 1,
      "process_id": 98244
    },
    "duration": 178000,
    "start": 1635361523989149000
  }]]