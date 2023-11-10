## Checklist

- [ ] Change(s) are motivated and described in the PR description.
- [ ] Testing strategy is described if automated tests are not included in the PR.
- [ ] Risk is outlined (performance impact, potential for breakage, maintainability, etc).
- [ ] Change is maintainable (easy to change, telemetry, documentation).
- [ ] [Library release note guidelines](https://ddtrace.readthedocs.io/en/stable/releasenotes.html) are followed. If no release note is required, add label `changelog/no-changelog`.
- [ ] Documentation is included (in-code, generated user docs, [public corp docs](https://github.com/DataDog/documentation/)).
- [ ] Backport labels are set (if [applicable](https://ddtrace.readthedocs.io/en/latest/contributing.html#backporting))

## Reviewer Checklist

- [ ] Title is accurate.
- [ ] No unnecessary changes are introduced.
- [ ] Description motivates each change.
- [ ] Avoids breaking [API](https://ddtrace.readthedocs.io/en/stable/versioning.html#interfaces) changes unless absolutely necessary.
- [ ] Testing strategy adequately addresses listed risk(s).
- [ ] Change is maintainable (easy to change, telemetry, documentation).
- [ ] Release note makes sense to a user of the library.
- [ ] Reviewer has explicitly acknowledged and discussed the performance implications of this PR as reported in the benchmarks PR comment.
- [ ] Backport labels are set in a manner that is consistent with the [release branch maintenance policy](https://ddtrace.readthedocs.io/en/latest/contributing.html#backporting)
- [ ] If this PR touches code that signs or publishes builds or packages, or handles credentials of any kind, I've requested a review from `@DataDog/security-design-and-guidance`.
- [ ] This PR doesn't touch any of that.
