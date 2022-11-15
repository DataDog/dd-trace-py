Release Note Guidelines
=======================
The guidelines below describe how to ensure consistency and clarity in release notes.

Release notes are the first product documentation a user will see when updating the library. Therefore, we must take care to ensure the quality of release notes.

For every pull request that changes how a user interacts with the library, a pull request should include a release note entry.


Does the change require a release note?
+++++++++++++++++++++++++++++++++++++++

A release note is required if a PR is user-facing, or if it meets any of the following conditions:
- [Breaking change to the public API](https://ddtrace.readthedocs.io/en/stable/versioning.html#release-versions)
- New feature
- Bug fix
- Deprecations
- Dependency upgrades

Otherwise, a release note is not required. In these cases, the pull request must be labelled ``changelog/no-changelog``.
Examples of when a release note is not required are:
- CI chores (e.g., upgrade/pinning dependency versions to fix CI)
- Changes to internal API (Non-public facing, or not-yet released components/features)

How to Generate a Release Note
++++++++++++++++++++++++++++++
Release notes are generated with the command line tool [reno](https://docs.openstack.org/reno/latest/user/usage.html#creating-new-release-notes)
which can be used with riot::

    $ riot run reno new <title-slug>

The <title-slug> is used as the prefix for a new file created in ``releasenotes/notes``.
The <title-slug> is used internally and is not visible in the the product documentation.

The format of the <title-slug> must be:
- number of words should not exceed 5
- words are separated with hyphens
- lowercase

For example:
- fix-aioredis-catch-cancellederror
- deprecate-tracer-writer
- add-graphene-support
- internalize-ddtrace-utils

Release Note Sections
+++++++++++++++++++++

Generated release note files are templates and include all possible categories.
All irrelevant sections should be removed for the final release note.
Once finished, the release note should be committed with the rest of the changes.

Features: New features such as a new integration or component.
Example::

  features:
  - |
    graphene: Adds support for ``graphene>=2``. `See the graphql documentation <https://ddtrace.readthedocs.io/en/1.6.0/integrations.html#graphql>`_
    for more information.

Upgrade: Enhanced functionality or if dependencies are upgraded. Also used for if components are removed.
Usually includes instruction or recommendation to user in regards to how to adjust to the new change.
Example::

  upgrade:
  - |
    tracing: Use ``Span.set_tag_str()`` instead of ``Span.set_tag()`` when the tag value is a
    text type as a performance optimization in manual instrumentation.

Deprecations: Warning of a component being removed from the public API in the future.
Example::

  deprecations:
  - |
    tracing: ``ddtrace.Span.meta`` has been deprecated. Use ``ddtrace.Span.get_tag`` and ``ddtrace.Span.set_tag`` instead.

Fixes: Bug fixes.
Example::

  fixes:
  - |
    django: Fixes an issue where a manually set ``django.request`` span resource would get overwritten by the integration.

Other: Any change which does not fall into any of the above categories, which should not happen often.
Example::

  other:
  - |
    docs: Adds documentation on how to use Gunicorn with the ``gevent`` worker class.

Prelude: Not required for every change. Required for major changes such as a new component or new feature
which would benefit the user by providing additional context or theme.
Example::

  prelude: >
    dynamic instrumentation: Dynamic instrumentation allows instrumenting a running service dynamically
    to extract runtime information that could be useful for, e.g., debugging
    purposes, or to add extra metrics without having to make code changes and
    re-deploy the service. See https://ddtrace.readthedocs.io/en/1.6.0/configuration.html
    for more details.
  features:
  - |
    dynamic instrumentation: Introduces the public interface for the dynamic instrumentation service. See
    https://ddtrace.readthedocs.io/en/1.6.0/configuration.html for more details.

Release Note Formatting
+++++++++++++++++++++++

In general, a release note entry should follow the following format::

    ---
    <section>:
      - |
        scope: note

Scope
~~~~~

This is a one-word scope shared between the release note and the conventional commit format used
in the pull request title. This should the name of the library component, sub-component or integration
that is impacted by this change. This should not be capitalized unless it is an acronym.

To ensure consistency in component naming, the convention in referring to components is as follows:

- Tracer: tracing
- Profiler: profiling
- Application security monitoring: ASM
- Dynamic instrumentation: dynamic instrumentation
- CI Visibility: CI visibility
- Integrations: integration_name

Note
~~~~

The note is a brief description of the change. It should follow sentence-case capitalization.
The note should also follow valid restructured text (RST) formatting.

- Features: Use present tense with the following format::

  <scope>: This introduces <new_feature_or_component>.

- Upgrade: Use present tense with the following formats::

  <scope>: This upgrades <present tense explanation>. With this upgrade, <actionable_step_for_user>.
  <scope>: <affected_code> has been removed. As an alternative to <affected_code>, you can use <alternative> instead.

- Deprecations: Use present tense for when deprecation/removal actually happens and future tense for
when deprecation/removal is planned to happen. Include deprecation/removal timeline,
as well as workarounds and alternatives in the following format::

  <scope>: <affected_code> is deprecated and will be removed in <version_to_be_removed>. As an alternative to <affected_code>, you can use <alternative> instead.

- Fixes: Use past tense for the problem and present tense for the fix and solution in the following format::

  <scope>: This fix resolves an issue where <ABC bug> caused <XYZ situation>.

- Other: Since changes falling into this category are likely rare and not very similar to each other,
no specific format other than a required scope is provided.
The author is requested to use their best judgement to ensure a quality release note.

- Prelude: Usually in tandem with a new feature or major change. No specific format other than a required scope
is provided and again the author is requested to use their best judgement to provide context and background
for the major change or new scope.

Release Note Style Guidelines
+++++++++++++++++++++++++++++

The main goal of a release note is to provide a brief overview of a change.
If necessary, we can also provide actionable steps to the user.

The release note should clearly communicate what the change is, why the change was made,
and how a user can migrate their code.

Release notes should:
- Use plain language
- Avoid overly technical language
- Be concise
- Include actionable steps with the necessary code changes
- Include relevant links (bug issues, upstream issues or release notes, documentation pages)
- Before using specifically acronyms and terminology specific to Datadog, a release note must first introduce them with a definition or explanation.

Release notes should not:
- Be vague. Example: ``fixes an issue in tracing``.
- Use acronyms liberally. If an acronym will be used multiple times in a release note, the acronym should be introduced in the first occurrence of the full term in parentheses immediately after the full term.
- Use dynamic links (``stable/latest/1.x`` URLs). Instead, use static links (specific version, commit hash/tag) whenever possible so that they don't break in the future.

