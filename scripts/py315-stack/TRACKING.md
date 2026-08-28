# Python 3.15 — follow-up tracking (post-split)

Tracks work **outside** the 5-PR profiling stack ([`PROFILING_STACK.md`](PROFILING_STACK.md))
that is still needed for full py3.15 parity. Core profiling code lives in
#19267 → #19269 → #19270 → #19272 → #19273.

**Last updated:** 2026-08-27

## Status summary

| ID | Item | Priority | Status | Next step |
|----|------|----------|--------|-----------|
| GAP-01 | Lib-injection SSI ([#17977](https://github.com/DataDog/dd-trace-py/pull/17977)) | P1 | **Pushed** `vlad/315-lib-injection-ssi` | Open PR after stack merges |
| GAP-02 | Profiling release note | P1 | **Pushed** `vlad/315-profiling-release-note` | Merge with profiling ship PR |
| GAP-03 | Dev tooling + runbook | P2 | **#19273** (this layer) | Stack tip |
| GAP-04 | `test_uwsgi.py` type-ignore cleanup | P3 | **Won't fix** | mypy requires the ignores |

### Follow-ups (outside the 5-PR stack)

GAP-01 / GAP-02 stay off this chain. #19273 is stack tip (GAP-03).

---

## GAP-01 — Lib-injection SSI for Python 3.15

**Jira:** [PROF-14439](https://datadoghq.atlassian.net/browse/PROF-14439)  
**Branch:** `vlad/315-lib-injection-ssi` (rebased on stack tip, cherry-pick of #17977)

- [x] `lib-injection/sources/sitecustomize.py` — max `(3, 15)` → `(3, 16)`
- [x] `lib-injection/dl_wheels.py` — add `"3.15"` to `supported_versions`
- [ ] Open PR (merge **after** profiling stack); closes #17813

---

## GAP-02 — Profiling release note

**Branch:** `vlad/315-profiling-release-note`  
**File:** `releasenotes/notes/profiling-python315-support-5910a6a4e623716b.yaml`

- [x] Reno fragment written (GIL-only; free-threading not validated)
- [ ] Merge with PR that lifts `setup.py` profiling native gate for 3.15

---

## GAP-03 — Profiling dev tooling + CPython upgrade runbook

**Branch:** `vlad/315-profiling-dev-tooling`

- [x] Port scripts + docs from `vlad/ddtracepy-upgrade-py-315-docs`
- [x] Link runbook from `docs/contributing.rst` + `docs/contributing-testing.rst`
- [x] Open PR: [#19273](https://github.com/DataDog/dd-trace-py/pull/19273)

---

## GAP-04 — `test_uwsgi.py` type-ignore cleanup

**Won't fix** — removing ignores fails mypy (`attr-defined`, `unreachable`).

---

## Stack hygiene

Live stack is the five PRs in [`PROFILING_STACK.md`](PROFILING_STACK.md).
Do **not** retarget [#17849](https://github.com/DataDog/dd-trace-py/pull/17849) onto this stack.
`DRAFT_PRS.md` / `rebuild-stack.sh` still describe the older 14-PR chain (historical).

---

## Archived PRs — no further audit

#17446, #17055, #17531, #17532, #17294, #17295, #17596, #17730, #18146, #17978, #17977, #17847, #17792
