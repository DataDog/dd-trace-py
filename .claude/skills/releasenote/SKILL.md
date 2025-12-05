---

name: releasenote
description: >
  Create or update release notes for changes in the current branch using Reno,
  following dd-trace-py's conventions and the guidelines in docs/releasenotes.rst.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - TodoWrite

---

## When to Use This Skill

Use this skill whenever:

* The user asks to create a new release note.
* The user asks to update or modify an existing release note.
* The user wants a summary of changes that is intended to be inserted into a release note file.

---

## Key Principles

* Follow strictly the conventions described in `docs/releasenotes.rst` and any Reno-related docs in the repo.
* ALWAYS use `riot` (not `reno` directly) to generate the new release note skeleton.
* If the user does not specify the release-note category (`feature`, `bugfix`, `deprecation`, `breaking-change`, `misc`, etc.), ask first.
* Release notes must:
  * Be one Reno fragment per change / PR, unless the change explicitly belongs inside an existing release note.
  * Use a slug that is short, clear, and uses hyphens.
  * Appear under `releasenotes/notes/` with a `.yaml` suffix.
* If updating an existing fragment, search for a fragment that matches the topic or ticket before creating a new one.
* Ensure the wording is:
  * Clear, concise, and user-facing.
  * Describes *what changed* and *why users should care*.
  * Avoids internal-only terminology unless necessary.

## Interaction Rules

* Before creating anything:

  1. Confirm the category.
  2. Confirm the title-slug if the user hasn't provided one.
  3. Confirm whether the release note is for a new fragment or an update.

* If the user wants to modify a note:
  * Search for the matching fragment using `ls releasenotes/notes/` or grep keywords.
  * Open the file and update only the content the user mentions.


## Quick Start

### Create a new release note

```bash
riot run reno new <title-slug>
```

After creation, modify the generated YAML fragment to include the content under the correct section:

```yaml
features:
  - |
    <description of the new feature>

# or

fixes:
  - |
    <description of the bug fix>
```

### List existing release notes

```bash
ls releasenotes/notes/
```

### Find a release note containing a keyword

```bash
grep -R "<keyword>" releasenotes/notes/
```

---

## Best Practices for Note Content

* Start with an action verb: *Add…*, *Fix…*, *Improve…*, *Deprecate…*
* Reference PR or issue numbers only if relevant (e.g., "(#12345)").
* If the change requires user action, highlight it clearly.
* Avoid long paragraphs; prefer concise bullet-style explanations.

---

## Optional Enhancements

If you want, you can add:

### Validation checks

* Ensure the repo is clean (`git status`).
* Confirm that the working directory is at the repo root.
* Warn if a release note already exists for the same issue/PR.

### Automation scaffolding

* Automatically propose a slug from the branch name.
* Suggest the best category based on commit diff keywords.

---

If you'd like, I can also:

✔ generate a stricter version (more guardrails)
✔ generate a shorter version (minimal skill spec)
✔ help you convert this to Anthropic’s new "Tool Use Skills" format
✔ help you create automated tests or examples for this skill

Just tell me!


## When to Use This Skill

Use this skill when:
- You are asked to create a new release note
- You are asked to update an existing release note

## Key Principles

- Strictly follow what is described in docs/releasenotes.rst
- ALWAYS use riot to generate the new release note
- If the user does not specify it, ask whether it is a fix, a feature, etc.

## Quick Start

Create the release note:
```bash
riot run reno new <title-slug>
```
