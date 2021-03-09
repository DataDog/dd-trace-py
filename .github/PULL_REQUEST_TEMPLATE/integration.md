# Integration
This PR adds support for [`<integration>`](<!--link to relevant integration docs-->).

## Links
<!-- Add any helpful links here for your and the reviewer's benefit -->

- Integration docs: <!-- add link here -->
- Corp docs PR: <!-- add link here -->

## Checklist
<!-- Delete any entries that are not applicable to the integration -->

- [ ] Usage and configuration documentation added in `__init__.py`, `docs/index.rst` and `docs/integrations.rst`.
- [ ] [Corp docs](https://github.com/Datadog/documentation) PR to add new integration to documentation.
- [ ] Span metadata
  - [ ] Service (use [`int_service`](https://github.com/DataDog/dd-trace-py/blob/90d1d5981c72ea312c21ac04e5be47521d0f0f2e/ddtrace/contrib/trace_utils.py#L55) or [`ext_service`](https://github.com/DataDog/dd-trace-py/blob/90d1d5981c72ea312c21ac04e5be47521d0f0f2e/ddtrace/contrib/trace_utils.py#L87)).
  - [ ] Span type should be one of [these](https://github.com/DataDog/dd-trace-py/blob/90d1d5981c72ea312c21ac04e5be47521d0f0f2e/ddtrace/ext/__init__.py#L7).
  - [ ] Resource
  - [ ] Measured tag
  - [ ] Sample analytics
- [ ] Global configuration
  - [ ] `ddtrace.config` entry is specified.
  - [ ] Environment variables are provided for config options.
- [ ] Instance configuration
  - [ ] Pin overriding.
  - [ ] Service name override (if applicable).
- [ ] Async
  - [ ] Span parenting behaves as expected.
  - [ ] Context propagation across async contexts.
- [ ] HTTP
  - [ ] Distributed tracing propagation is implemented.
  - [ ] Use [`trace_utils.set_http_meta`](https://github.com/DataDog/dd-trace-py/blob/90d1d5981c72ea312c21ac04e5be47521d0f0f2e/ddtrace/contrib/trace_utils.py#L143-L152) to set http tags
- [ ] Tests
  - [ ] Use `pytest` fixtures found in `tests/conftest.py` or the test helpers on `TracerTestCase` if
    writing `unittest` style test cases.
  - [ ] Tests are provided for all the above.
  - [ ] Tests are added to CI (`.circleci/config.yml`).
  - [ ] Functionality is maintained from original library.
  - [ ] Patch test cases are added (see `test_django_patch.py` for an example).
  - [ ] All Python versions that the library supports are tested.
  - [ ] All significant library versions (including the latest) are tested. This typically includes every minor release going back a few years.
