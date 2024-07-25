## Checklist
- [ ] PR author has checked that all the criteria below are met
- The PR description includes an overview of the change
- The PR description articulates the motivation for the change
- The change includes tests OR the PR description describes a testing strategy
- The PR description notes risks associated with the change, if any
- Newly-added code is easy to change
- The change follows the [library release note guidelines](https://ddtrace.readthedocs.io/en/stable/releasenotes.html)
- The change includes or references documentation updates if necessary
- Backport labels are set (if [applicable](https://ddtrace.readthedocs.io/en/latest/contributing.html#backporting))

## Reviewer Checklist
- [ ] Reviewer has checked that all the criteria below are met 
- Title is accurate
- All changes are related to the pull request's stated goal
- Avoids breaking [API](https://ddtrace.readthedocs.io/en/stable/versioning.html#interfaces) changes
- Testing strategy adequately addresses listed risks
- Newly-added code is easy to change
- Release note makes sense to a user of the library
- If necessary, author has acknowledged and discussed the performance implications of this PR as reported in the benchmarks PR comment
- Backport labels are set in a manner that is consistent with the [release branch maintenance policy](https://ddtrace.readthedocs.io/en/latest/contributing.html#backporting)
