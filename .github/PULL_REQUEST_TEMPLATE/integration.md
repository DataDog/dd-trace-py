# Integration
This PR adds support for [`<integration>`](<!--link to relevant integration docs-->).

## Links
<!-- Add any helpful links here for your and the reviewer's benefit -->

- Integration docs: <!-- add link here -->
- Corp docs PR: <!-- add link here -->

## Checklist
- [ ] Documentation added in `__init__.py`.
- [ ] [Corp docs](https://github.com/Datadog/documentation) are updated.
- [ ] Entry added to `CHANGELOG.md`.

### Testing
- [ ] Tests are added to CI.
- [ ] Functionality is maintained from original library.
- [ ] All Python versions that the library supports are tested.
- [ ] All relevant library versions (including the latest) are tested.
- [ ] Global configuration
  - [ ] Environment variables.
  - [ ] `ddtrace.config`.
- [ ] Instance configuration
  - [ ] Pin overriding.
- [ ] Async (if applicable)
  - [ ] Span parenting behaves as expected.
