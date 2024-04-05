## Checklist

- [ ] Change(s) are motivated and described in the PR description
- [ ] Testing strategy is described if automated tests are not included in the PR
- [ ] Risks are described (performance impact, potential for breakage, maintainability)
- [ ] Change is maintainable (easy to change, telemetry, documentation)
- [ ] [Library release note guidelines](https://ddtrace.readthedocs.io/en/stable/releasenotes.html) are followed or label `changelog/no-changelog` is set
- [ ] Documentation is included (in-code, generated user docs, [public corp docs](https://github.com/DataDog/documentation/))
- [ ] Backport labels are set (if [applicable](https://ddtrace.readthedocs.io/en/latest/contributing.html#backporting))
- [ ] If this PR changes the public interface, I've notified `@DataDog/apm-tees`.

## Reviewer Checklist

- [ ] Title is accurate
- [ ] All changes are related to the pull request's stated goal
- [ ] Description motivates each change
- [ ] Avoids breaking [API](https://ddtrace.readthedocs.io/en/stable/versioning.html#interfaces) changes
- [ ] Testing strategy adequately addresses listed risks
- [ ] Change is maintainable (easy to change, telemetry, documentation)
- [ ] Release note makes sense to a user of the library
- [ ] Author has acknowledged and discussed the performance implications of this PR as reported in the benchmarks PR comment
- [ ] Backport labels are set in a manner that is consistent with the [release branch maintenance policy](https://ddtrace.readthedocs.io/en/latest/contributing.html#backporting)
