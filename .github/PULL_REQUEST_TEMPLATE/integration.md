# Integration
This PR adds support for [`<integration>`](<!--link to relevant integration docs-->).

## Links
<!-- Add any helpful links here for your and the reviewer's benefit -->

- Integration docs: <!-- add link here -->
- Corp docs PR: <!-- add link here -->

## Checklist
- [ ] Usage and configuration documentation added in `__init__.py`.
- [ ] [Corp docs](https://github.com/Datadog/documentation) are updated.
- [ ] Distributed tracing propagation is implemented (if applicable).
- [ ] Span metadata
  - [ ] Span type
  - [ ] Resource
- [ ] Global configuration
  - [ ] Inherits `DD_SERVICE` (if applicable).
  - [ ] Environment variables are provided for config options.
  - [ ] `ddtrace.config`.
- [ ] Instance configuration
  - [ ] Pin overriding.
  - [ ] Service name override (if applicable).
- [ ] Async (if applicable)
  - [ ] Span parenting behaves as expected.
  - [ ] Context propagation across async boundaries.
- [ ] HTTP (if applicable)
  - [ ] 500-level responses are tagged as errors.
- [ ] Web (if applicable)
  - [ ] Request headers specified in the config are stored.
- [ ] Tests
  - [ ] Tests are provided for all of the above.
  - [ ] Tests are added to CI (`.circleci/config.yml`).
  - [ ] Functionality is maintained from original library.
  - [ ] Patch test cases are added (see `test_django_patch.py` for an example).
  - [ ] All Python versions that the library supports are tested.
  - [ ] All relevant library versions (including the latest) are tested.
