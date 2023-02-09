## Checklist

- [ ] Change(s) are motivated and described in the PR description.
- [ ] Testing strategy is described if automated tests are not included in the PR.
- [ ] Risk is outlined (performance impact, potential for breakage, maintainability, etc).
- [ ] Change is maintainable (easy to change, telemetry, documentation).
- [ ] [Library release note guidelines](https://ddtrace.readthedocs.io/en/stable/contributing.html#Release-Note-Guidelines) are followed.
- [ ] Documentation is included (in-code, generated user docs, [public corp docs](https://github.com/DataDog/documentation/)).
- [ ] Author is aware of the performance implications of this PR as reported in the benchmarks PR comment.

## Reviewer Checklist

- [ ] Title is accurate.
- [ ] No unnecessary changes are introduced.
- [ ] Description motivates each change.
- [ ] Avoids breaking [API](https://ddtrace.readthedocs.io/en/stable/versioning.html#interfaces) changes unless absolutely necessary.
- [ ] Testing strategy adequately addresses listed risk(s).
- [ ] Change is maintainable (easy to change, telemetry, documentation).
- [ ] Release note makes sense to a user of the library.
- [ ] Reviewer is aware of, and discussed the performance implications of this PR as reported in the benchmarks PR comment.
