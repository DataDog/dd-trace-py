# Python 3.15 — follow-up tracking (post-split)

Tracks work **outside** the 11-PR stack that is still needed for full py3.15 parity.
Core profiling/tracing code lives in the stack; items here are deferred, optional polish,
or blocked on stack merge/CI.

**Last updated:** 2026-07-22 (branches pushed locally — open PRs via compare links below)

## Status summary

| ID | Item | Priority | Status | Next step |
|----|------|----------|--------|-----------|
| GAP-01 | Lib-injection SSI ([#17977](https://github.com/DataDog/dd-trace-py/pull/17977)) | P1 | **Pushed** `vlad/315-lib-injection-ssi` | Open PR after stack merges |
| GAP-02 | Profiling release note | P1 | **Pushed** `vlad/315-profiling-release-note` | Merge with profiling ship PR |
| GAP-03 | Dev tooling + runbook | P2 | **Pushed** `vlad/315-profiling-dev-tooling` | Open PR now |
| GAP-04 | `test_uwsgi.py` type-ignore cleanup | P3 | **Won't fix** | mypy requires the ignores |

### Open drafts (Prev/Next pre-filled in body)

| # | Branch | PR body | Open draft |
|---|--------|---------|------------|
| 12 | `vlad/315-profiling-dev-tooling` | [`12-vlad-315-profiling-dev-tooling.md`](pr-bodies/12-vlad-315-profiling-dev-tooling.md) | [compare](https://github.com/DataDog/dd-trace-py/compare/vlad/ddtracepy-315-profiling-asyncio-monitoring...vlad/315-profiling-dev-tooling?expand=1&title=docs%28profiling%29%3A%20py3.15%20dev%20tooling%20and%20CPython%20upgrade%20runbook%20%28split%2012/14%29&body=%2A%2AStack%20navigation%3A%2A%2A%20%5B%E2%86%90%20Prev%5D%28https%3A//github.com/DataDog/dd-trace-py/pull/18389%29%20%7C%20Next%20%E2%86%92%20_%28PR%20%2313%20%E2%80%94%20set%20PR13%20in%20pr-numbers.env%29_%0A%0A%23%23%20Summary%0A%0AProfiling%20bring-up%20scripts%20%28%60verify_profiler_compatibility%60%2C%20%60run-profiling-tests%60%29%2C%20compatibility%20baselines%2C%20and%20Echion%20migration%20runbook%20%28GAP-03%29.%0A%0A%23%23%20Test%20plan%0A%0A-%20%5B%20%5D%20%60scripts/run-profiling-tests%20--check-only%60%20passes%20on%203.15%20%28when%20available%29%0A-%20%5B%20%5D%20Docs%20build%20/%20link%20check%0A%0A&draft=1) |
| 13 | `vlad/315-lib-injection-ssi` | [`13-vlad-315-lib-injection-ssi.md`](pr-bodies/13-vlad-315-lib-injection-ssi.md) | [compare](https://github.com/DataDog/dd-trace-py/compare/vlad/ddtracepy-315-profiling-asyncio-monitoring...vlad/315-lib-injection-ssi?expand=1&title=chore%28py-315%29%3A%20enable%20lib-injection%20SSI%20for%20Python%203.15%20%28split%2013/14%29&body=%2A%2AStack%20navigation%3A%2A%2A%20%E2%86%90%20Prev%20_%28PR%20%2312%20%E2%80%94%20set%20PR12%20in%20pr-numbers.env%29_%20%7C%20Next%20%E2%86%92%20_%28PR%20%2314%20%E2%80%94%20set%20PR14%20in%20pr-numbers.env%29_%0A%0A%23%23%20Summary%0A%0ABump%20SSI%20allow-list%20and%20wheel%20download%20list%20for%203.15%20auto-instrumentation.%20Supersedes%20%2317977%20%28GAP-01%29.%20Merge%20after%20profiling%20stack.%0A%0A%23%23%20Test%20plan%0A%0A-%20%5B%20%5D%20lib-injection%20CI%20green%0A-%20%5B%20%5D%20Merge%20only%20after%20profiling%20natives%20build%20on%203.15%0A%0A&draft=1) |
| 14 | `vlad/315-profiling-release-note` | [`14-vlad-315-profiling-release-note.md`](pr-bodies/14-vlad-315-profiling-release-note.md) | [compare](https://github.com/DataDog/dd-trace-py/compare/vlad/ddtracepy-315-profiling-asyncio-monitoring...vlad/315-profiling-release-note?expand=1&title=docs%28releasenotes%29%3A%20profiling%20Python%203.15%20support%20note%20%28split%2014/14%29&body=%2A%2AStack%20navigation%3A%2A%2A%20%E2%86%90%20Prev%20_%28PR%20%2313%20%E2%80%94%20set%20PR13%20in%20pr-numbers.env%29_%20%7C%20Next%20%E2%86%92%0A%0A%23%23%20Summary%0A%0ACustomer-facing%20Reno%20fragment%20for%20profiling%20on%203.15%20%28GIL-enabled%3B%20free-threading%20not%20validated%29.%20Merge%20with%20profiling%20ship%20PR%20%28GAP-02%29.%0A%0A%23%23%20Test%20plan%0A%0A-%20%5B%20%5D%20%60riot%20run%20reno%60%20validates%20fragment%0A-%20%5B%20%5D%20Merge%20with%20PR%20that%20lifts%20profiling%20native%20gate%20for%203.15%0A%0A&draft=1) |

After opening each PR, set `PR12`/`PR13`/`PR14` in [`pr-numbers.env`](pr-numbers.env) and re-run [`generate-pr-urls.sh`](generate-pr-urls.sh) to wire live Prev/Next links across the chain.

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
- [ ] Open PR: `docs(profiling): py3.15 dev tooling and CPython upgrade runbook`

---

## GAP-04 — `test_uwsgi.py` type-ignore cleanup

**Won't fix** — removing ignores fails mypy (`attr-defined`, `unreachable`).

---

## Stack hygiene (EMU blocks `gh pr create`)

See [`DRAFT_PRS.md`](DRAFT_PRS.md):

- [ ] Create draft PRs 1, 3–6 via compare URLs
- [ ] Retarget [#17849](https://github.com/DataDog/dd-trace-py/pull/17849) → `gab/315-monitoring-multiplexer`
- [ ] Reopen + retarget [#18488](https://github.com/DataDog/dd-trace-py/pull/18488)–[#18389](https://github.com/DataDog/dd-trace-py/pull/18389)

---

## Archived PRs — no further audit

#17446, #17055, #17531, #17532, #17294, #17295, #17596, #17730, #18146, #17978, #17977, #17847, #17792
