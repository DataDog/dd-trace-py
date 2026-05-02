## Description

Part of the Python 3.15 integration parity effort
(parent tracker: <!-- fill in parent tracker issue, e.g. #12345 -->).

Closes #<!-- sub-issue number for this row, e.g. #12346 -->

<!-- The `Closes #<sub-issue>` line above auto-closes the per-row sub-issue
     on merge, which auto-flips the parent tracker's checkbox. Keep both
     references — the parent gives readers context, the `Closes` does the
     bookkeeping. -->

This PR enables the **`<integration>`** integration on Python 3.15.

<!-- One-paragraph summary of the upstream blocker and what changed.
     Example: "Bumps `tiktoken` to 0.x.y, which adds Python 3.15 wheels
     (https://github.com/openai/tiktoken/releases/tag/x.y). Lifts the
     `max_version="3.13"` cap on the `<integration>` venv in riotfile.py." -->

## Checklist

- [ ] Bumped upstream pin in `riotfile.py` to a version that supports Python 3.15
- [ ] Lifted `max_version="3.13"` / `"3.14"` cap on the affected venv(s) (if present)
- [ ] Ran `riot generate <suite-pattern>` and committed the regenerated `.riot/requirements/*.txt` lockfiles
- [ ] Ran the suite locally on 3.15 via `scripts/run-tests <suite>` (paste a link or summary of the result)
- [ ] Updated `supported_versions_table.csv` / `supported_versions_output.json` if integration min/max versions changed
- [ ] Release note added under `releasenotes/notes/`, **or** PR labeled `changelog/no-changelog` (test/CI-only changes)

## Testing

<!-- Paste local 3.15 run output, CI link, or both. -->

## Risks

<!-- Note any risks. Common risks for this type of PR:
     - upstream version bump breaks existing 3.9-3.14 coverage
     - lockfile regen accidentally pulls in unrelated bumps
     - new Python 3.15 deprecation warnings -->

## Additional Notes

<!-- Any other context the reviewer needs. -->
