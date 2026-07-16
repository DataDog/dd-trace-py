# Step 13: final > observability

- Type: required final gate

## Instructions

Run the Pyramid 2.1 application/system-test reproduction from `TASK.md` against the changed tracer and store the
captured local test-agent payload or trace JSON. Inspect the failing unvalidated-redirect route and verify that
the request produces exactly one `UNVALIDATED_REDIRECT` vulnerability with the expected source/location data,
while retaining the existing Pyramid request trace shape. Unit tests or self-reported success alone do not pass
this gate; unavailable application or trace-capture infrastructure makes it BLOCKED.

This gate is verification-only: do not edit product code here. Record exact commands,
exit status, source revision, and artifact paths in a bounded `evidence/13/summary.md`.
Keep raw output in `evidence/13/raw/`. All final gates must validate the same source
state; after any code change, clear their checkmarks and restart at `final > build`.
