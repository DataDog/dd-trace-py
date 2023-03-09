Release Notes
=============
Release notes are the primary product documentation a user will see when updating the library. Therefore, we must take care to ensure the quality of release notes.

A release note entry should be included for every pull request that changes how a user interacts with the library.

Requiring a Release Note
++++++++++++++++++++++++

A release note is **required** if a PR is user-impacting, or if it meets any of the following conditions:

* `Breaking change to the public API <https://ddtrace.readthedocs.io/en/stable/versioning.html#release-versions>`_
* New feature
* Bug fix
* Deprecations
* Dependency upgrades

Otherwise, a release note is not required.
Examples of when a release note is **not required** are:

* CI chores (e.g., upgrade/pinning dependency versions to fix CI)
* Changes to internal API (Non-public facing, or not-yet released components/features)

Release Note Style Guidelines
+++++++++++++++++++++++++++++

The main goal of a release note is to provide a brief overview of a change.
If necessary, we can also provide actionable steps to the user.

The release note should clearly communicate what the change is, why the change was made,
and how a user can migrate their code.

The release note should also clearly distinguish between announcements and user instructions. Use:

* Past tense for previous/existing behavior (ex: ``resulted, caused, failed``)
* Third person present tense for the change itself (ex: ``adds, fixes, upgrades``)
* Active present infinitive for user instructions (ex: ``set, use, add``)

Release notes should:

* Use plain language.
* Be concise.
* Include actionable steps with the necessary code changes.
* Include relevant links (bug issues, upstream issues or release notes, documentation pages).
* Use full sentences with sentence-casing and punctuation.
* Before using Datadog specific acronyms/terminology, a release note must first introduce them with a definition.

Release notes should not:

* Be vague. Example: ``fixes an issue in tracing``.
* Use overly technical language.
* Use dynamic links (``stable/latest/1.x`` URLs). Instead, use static links (specific version, commit hash) whenever possible so that they don't break in the future.

Generating a Release Note
+++++++++++++++++++++++++
Release notes are generated with the command line tool ``reno`` which can be used with riot::

    $ riot run reno new <title-slug>

The ``<title-slug>`` is used as the prefix for a new file created in ``releasenotes/notes``.
The ``<title-slug>`` is used internally and is not visible in the the product documentation.

Generally, the format of the ``<title-slug>`` is lowercase words separated by hyphens.

For example:

* ``fix-aioredis-catch-canceled-error``
* ``deprecate-tracer-writer``

Release Note Sections
+++++++++++++++++++++

Generated release note files are templates and include all possible categories.
All irrelevant sections should be removed for the final release note.
Once finished, the release note should be committed with the rest of the changes.

* Features: New features such as a new integration or component. For example::

    features:
    - |
      graphene: Adds support for ``graphene>=2``. `See the graphql documentation <https://ddtrace.readthedocs.io/en/1.6.0/integrations.html#graphql>`_
      for more information.

* Upgrade: Enhanced functionality or if dependencies are upgraded. Also used for if components are removed. Usually includes instruction or recommendation to user in regards to how to adjust to the new change. For example::

    upgrade:
    - |
      tracing: Use ``Span.set_tag_str()`` instead of ``Span.set_tag()`` when the tag value is a
      text type as a performance optimization in manual instrumentation.

* Deprecations: Warning of a component being removed from the public API in the future. For example::

    deprecations:
    - |
      tracing: ``ddtrace.Span.meta`` has been deprecated. Use ``ddtrace.Span.get_tag`` and ``ddtrace.Span.set_tag`` instead.

* Fixes: Bug fixes. For example::

    fixes:
    - |
      django: Fixes an issue where a manually set ``django.request`` span resource would get overwritten by the integration.

* Other: Any change which does not fall into any of the above categories. For example::

    other:
    - |
      docs: Adds documentation on how to use Gunicorn with the ``gevent`` worker class.

* Prelude: Not required for every change. Required for major changes such as a new component or new feature which would benefit the user by providing additional context or theme. For example::

    prelude: >
      dynamic instrumentation: Dynamic Instrumentation allows instrumenting a running service dynamically
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

This is a one-word scope, which is ideally the name of the library component, sub-component or integration
that is impacted by this change. This should not be capitalized unless it is an acronym.

To ensure consistency in component naming, the convention in referring to components is as follows:

* Tracer: ``tracing``
* Profiler: ``profiling``
* Application Security Monitoring: ``ASM``
* Dynamic Instrumentation: ``dynamic instrumentation``
* CI Visibility: ``CI visibility``
* Integrations: ``integration_name``

Note
~~~~

The note is a brief description of the change. It should consist of full sentence(s) with sentence-case capitalization.
The note should also follow valid restructured text (RST) formatting. See the template release note for
more details and instructions.

Compiling Release Notes
+++++++++++++++++++++++

To compile all release notes since a given version, do this::

    $ pip install reno
    $ brew install pandoc
    $ reno report --no-show-source | pandoc -f rst -t gfm --wrap=none | pbcopy

This will generate all release notes in the library's history in a single text string, ordered from latest to earliest.
