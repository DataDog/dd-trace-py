## Description
<!-- Briefly describe the change and why it was required. -->

<!-- If this is a breaking change, explain why it is necessary. Breaking changes must append `!` after the type/scope. See https://ddtrace.readthedocs.io/en/stable/contributing.html for more details. -->

## Checklist
- [ ] Add additional sections for `feat` and `fix` pull requests.
- [ ] [Library documentation](https://github.com/DataDog/dd-trace-py/tree/1.x/docs) and/or [Datadog's documentation site](https://github.com/DataDog/documentation/) is updated. Link to doc PR in description.

<!-- Copy and paste the relevant snippet based on the type of pull request -->

<!-- START feat -->

## Motivation
<!-- Expand on why the change is required, include relevant context for reviewers -->

## Design 
<!-- Include benefits from the change as well as possible drawbacks and trade-offs -->

## Testing strategy
<!-- Describe the automated tests and/or the steps for manual testing.

<!-- END feat -->

<!-- START fix -->

## Relevant issue(s)
<!-- Link the pull request to any issues related to the fix. Use keywords for links to automate closing the issues once the pull request is merged. -->

## Testing strategy
<!-- Describe any added regression tests and/or the manual testing performed. -->

<!-- END fix -->

## Reviewer Checklist
- [ ] Title is accurate.
- [ ] Description motivates each change.
- [ ] No unnecessary changes were introduced in this PR.
- [ ] Avoid breaking [API](https://ddtrace.readthedocs.io/en/stable/versioning.html#interfaces) changes unless absolutely necessary.
- [ ] Tests provided or description of manual testing performed is included in the code or PR.
- [ ] Release note has been added for fixes and features, or else `changelog/no-changelog` label added.
- [ ] All relevant GitHub issues are correctly linked.
- [ ] Backports are identified and tagged with Mergifyio.
