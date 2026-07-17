.. _release_notes:

Release Notes
=============

Release notes are the primary product documentation a user sees when updating the library.
Therefore, we must take care to ensure the quality of release notes.

A release note entry should be included for every pull request that changes how a user interacts
with the library.

A release note is **required** if a PR is user-impacting, or if it meets any of the following conditions:

* Breaking change to the public API
* New feature
* Bug fix
* Deprecations
* Dependency upgrades

Examples of when a release note is **not required** are:

* CI chores (e.g., upgrade/pinning dependency versions to fix CI)
* Changes to internal API (Non-public facing, or not-yet released components/features)

If it's unclear whether a change is customer-impacting, ask — don't guess unless the PR or
issue already states the customer impact.

After you've written a release note by following this guide, commit it to your change branch and
include it in your pull request.

Audience
--------

A release note is written for a customer upgrading the library, not for a dd-trace-py
contributor. It is **not** a summary of the PR, and it is **not** a place for internal
engineering context. That context (root cause, implementation approach, alternatives
considered, CI/test notes) belongs in the pull request description, not the release note.

Before writing, ask: "If a customer reads only this note, without opening the PR, can they
tell what changed, whether it affects them, and what to do about it?" If the answer requires
information you didn't include, add it. If the note contains detail a customer can't act on
(an internal function/class name, a data structure, an RFC/JIRA number, a CI job, "why we
implemented it this way"), remove it.

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

For a **fix**, state the customer-visible symptom, not the internal root cause, in present
tense: ``Fixes an issue where <user-visible behavior> occurs when <condition>.`` Do not name
the internal mechanism (cache, data structure, algorithm, class) unless that mechanism is
itself the public API being documented.

For a **feature or behavior change** that is configurable, or that changes a default, state the
current/new default and the exact environment variable, config option, or argument to use to
enable, disable, or configure it. Don't omit this when a toggle exists.

Release notes should:

* Use plain language.
* Be concise: 1-2 sentences for most fixes; longer only when a code example is required to show
  a new public API.
* Include actionable steps with the necessary code changes.
* Include relevant links (bug issues, upstream issues or release notes, documentation pages).
* Use full sentences with sentence-casing and punctuation.
* Before using Datadog specific acronyms/terminology, a release note must first introduce them with a definition.

Release notes should not:

* Be vague. Example: ``fixes an issue in tracing``.
* Use overly technical language.
* Use dynamic links (``stable/latest/1.x`` URLs). Instead, use static links (specific version, commit hash) whenever possible so that they don't break in the future.
* Reference internal-only identifiers: private/internal function or class names, internal tag
  prefixes (``_dd.*``), RFC/JIRA/ticket numbers, CI job names, or draft leftovers (``TODO``,
  unfinished sentences).
* Restate the PR description's engineering rationale ("why we chose this approach", "this
  required refactoring X").
* Describe a default, timeout, or behavior without checking it against the actual code/config —
  don't copy a number from memory or from the PR title.

Examples
~~~~~~~~

Good — fix, states the symptom, no internals::

    fixes:
      - |
        profiling: Fixes inconsistent handling of ``DD_PROFILING_MAX_FRAMES`` by clamping it
        before stack collection so samples stay within the backend's 600-location limit.

Good — new config surface, exact toggle and effect::

    features:
      - |
        AI Guard: This introduces per-integration kill-switch environment variables, extending
        the existing ``DD_AI_GUARD_OPENAI_ENABLED`` switch to the Anthropic and LangChain
        auto-instrumentations: ``DD_AI_GUARD_ANTHROPIC_ENABLED`` and
        ``DD_AI_GUARD_LANGCHAIN_ENABLED`` (both ``true`` by default). Set either to ``false``
        to disable AI Guard for that specific provider/framework.

Bad — leaks internal root cause and mechanism instead of the symptom::

    fixes:
      - |
        internal: Fixed a GC reference-cycle leak in PeriodicThread by breaking the cycle
        between the thread and its target callback.

    # Rewrite as (the "internal" scope is fine here — the fix isn't tied to one product;
    # what's wrong is naming the mechanism instead of the symptom):
    fixes:
      - |
        internal: Fixes a memory leak that causes memory usage to grow over time in
        long-running processes using background periodic tasks (e.g. trace stats reporting).

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
* Not tied to one product (e.g. core threading/fork-safety affecting several products): ``internal``

``internal`` is a legitimate scope for genuinely cross-cutting changes — it is not a
placeholder for when you didn't pick a scope. If a specific product/integration is affected,
use that scope instead.

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
