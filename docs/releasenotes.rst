.. _release_notes:

Release Notes
=============

Release notes are the primary product documentation a user sees when updating the library.
Therefore, we must take care to ensure the quality of release notes.

A release note entry should be included for every pull request that changes how a user interacts
with the library.

A release note is **required** if a PR is user-impacting, or if it meets any of the following conditions:

* :ref:`Breaking change to the public API<versioning_release>`
* New feature
* Bug fix
* Deprecations
* Dependency upgrades

Examples of when a release note is **not required** are:

* CI chores (e.g., upgrade/pinning dependency versions to fix CI)
* Changes to internal API (Non-public facing, or not-yet released components/features)

After you've written a release note by following this guide, commit it to your change branch and
include it in your pull request.

Style Guidelines
----------------

The main goal of a release note is to provide a brief overview of a change.
If necessary, it can also provide actionable steps to the user.

The release note should clearly communicate what the change is, why the change was made,
and any actions that users should take in response to the change.

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
-------------------------

You can generate a release note with the command line tool ``reno`` via ``riot``::

    $ riot run reno new <title-slug>

The ``<title-slug>`` is used as the prefix for a new file created in ``releasenotes/notes``.
The ``<title-slug>`` is used internally and is not visible in the the product documentation.

Generally, the format of the ``<title-slug>`` is lowercase words separated by hyphens, for
example ``fix-aioredis-catch-canceled-error`` or ``deprecate-tracer-writer``.

The abstract format of a release note is::

    category:
    - |
      scope: note

See any of the files in ``releasenotes/notes`` for examples.

Categories
----------

Generated release note files are templates and include all possible categories.
Remove all sections from your generated note except for the ones that apply to the
change you're documenting.

.. _release_notes_scope:

Scope
-----

The "scope" component of the release note is the name of the library component, sub-component or integration
that is impacted by the change.

The convention in referring to components is as follows:

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
=======================

To compile all release notes from the beginning of time to a given version, do this::

    $ pip install reno
    $ brew install pandoc
    $ git checkout <given version>
    $ reno report --no-show-source | pandoc -f rst -t gfm --wrap=none | pbcopy

This will generate all release notes in the library's history in a single text string, ordered from latest to earliest.
