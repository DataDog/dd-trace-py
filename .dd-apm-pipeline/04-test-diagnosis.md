# Step 4: test > diagnosis

- Type: agent
- Execution: required
- Objective: Analyze test failures and return structured diagnosis.

## Context Budget

Start from `PROGRESS.md` and prior `summary.md` receipts. Do not preload raw artifacts. Keep the
handoff at or below 200 lines and 20 KB. When discovery produces a complete inventory, store it
under this bundle's `evidence/<step>/raw/` and summarize only the ranked entries needed downstream.

## Prompt

<!-- Workflow: execute, Namespace: pyramid, Step: diagnosis -->

# Test Diagnosis Agent

<derive from repository or prior step: review_mode>

## Mission

Run tests for **pyramid** (version: **<derive from repository or prior step: package_version>**) and diagnose any failures. Return a structured diagnosis that identifies:

<derive from repository or prior step: version_note>
1. Whether tests pass or fail
2. The primary failure mode
3. Which skills and files the fixer agent should use

---

## Test Command

Run this command to execute tests:

```bash
<derive from repository or prior step: test_command>
```

Output will be auto-saved to `<derive from repository or prior step: attempt_dir>/test-output-N.log` (auto-incremented)

---

## Analyzing Test Output

### Parse Test Results

Extract pass/fail/skip counts from output and identify the primary failure pattern.

### Debug Pattern Guide (dd-trace-py)

Look for these patterns in the test output:

| Pattern | Meaning |
|---------|---------|
| `FAILED` with `AssertionError` | Span/tag assertion mismatch |
| `ModuleNotFoundError` | Missing dependency or wrong riot venv |
| `ConnectionRefusedError` / `ECONNREFUSED` | Docker service not running |
| `riot` errors or `venv` failures | Environment setup issue |
| `pytest.mark.skip` found | Forbidden -- tests must not be skipped |
| `_expected_llmobs_llm_span_event` mismatch | LLMObs tag extraction issue |
| `pop_traces()` returns empty | Spans not being created |
| `Timeout` or test hangs | Span created but not finished, or wrong assertion |

**Interpreting pytest output:**
- Look for `X passed, Y failed, Z errors` summary line
- `E` prefix lines show assertion details
- `>` prefix lines show the failing code line
- Stack traces point to exact file and line number

**Common dd-trace-py debug flags:**
- `DD_TRACE_DEBUG=true` enables verbose tracer logging
- `ddtrace-run` is the CLI entrypoint for tracing applications
- Tests use `riot` for virtualenv management via `scripts/run-tests`


---

## Failure Modes

### Base Failure Modes (All Languages)

#### `tags`
**Description:** Wrong/missing tags on spans

**When to use:** Spans are created but tests fail due to missing or incorrect tag values.

**Symptoms:**
- Test assertions fail on tag/meta/metrics values (e.g. `expected X to equal Y` on a tag)
- Tests time out waiting for the right tag value to arrive
- Test agent / FakeAgent receives spans but with the wrong shape

#### `spans_not_created`
**Description:** Spans not being created at all

**When to use:** Tests run but no spans reach the test agent despite instrumentation being configured.

**Symptoms:**
- Test agent / FakeAgent receives no spans for the operation under test
- Tests time out waiting for spans that never arrive
- Assertions on the spans list see it empty

#### `spans_not_finished`
**Description:** Spans created but not finishing properly

**When to use:** Spans reach the test agent but never close (no end time / duration).

**Symptoms:**
- Spans visible in the test agent but missing expected end time / duration
- Tests hang waiting for a completed-span assertion
- Trace fragments without a closing event

#### `startup_failure`
**Description:** Test environment/setup failure

**When to use:** Tests fail before any instrumentation runs.

**Symptoms:**
- Connection errors (ECONNREFUSED, etc.)
- Missing Docker services
- Module not found errors
- Tests fail immediately with setup errors

#### `unknown`
**Description:** Unknown or unclassified failure

**When to use:** The failure doesn't match other modes.

**Symptoms:**
- Failure doesn't match known patterns

## dd-trace-py Test Diagnosis

### Running Tests

**CRITICAL: Always use `scripts/run-tests`, never `pytest` or `riot` directly.**

```bash
scripts/run-tests tests/contrib/pyramid/
scripts/run-tests tests/contrib/pyramid/ -- -k "test_name"
```

### dd-trace-py Failure Modes

| Mode | Symptoms | What to check |
|------|----------|--------------|
| `helper_method_tests` | Tests call `_extract_*()` directly, no `test_spans.pop_traces()` | Tests must exercise real library calls |
| `weak_llmobs_assertions` | `assert mock_llmobs_writer.enqueue.called` only | Must use `_expected_llmobs_llm_span_event` |
| `weak_span_assertions` | `assert len(spans) >= 1` only | Must check span name, tags, resource |
| `shortcuts_detected` | `pytest.mark.skip` or empty test bodies | Never skip -- implement or remove |
| `improper_mocking` | Mocks public library API | Mock internal/I/O layer instead |

### APM vs LLMObs Test Separation

| Type | File pattern | Key assertion |
|------|-------------|---------------|
| APM | `test_*.py` | `test_spans.pop_traces()` + tag checks |
| LLMObs | `test_*_llmobs.py` | `_expected_llmobs_llm_span_event` |

### Reference Test Files

- APM: `tests/contrib/kafka/test_kafka.py`, `tests/contrib/psycopg/test_psycopg.py`
- LLMObs: `tests/contrib/anthropic/test_anthropic_llmobs.py`
- DSM: `tests/contrib/kafka/test_kafka_dsm.py`

---

## Files to Check

Common files involved in test failures:

<derive from repository or prior step: files_to_check>

---

## Guidelines

1. **Run tests first** - Don't guess, analyze actual output
2. **Be specific** - Identify the exact failure mode
3. **Recommend skills** - Choose skills that will help fix this specific issue, common + specific failure mode skills
4. **List files** - Tell the fixer which files to examine
5. **Summarize output** - Include key error lines in test_output_summary


## Expected Output Format

Output must be valid JSON matching this format:

```typescript
{
  success: boolean,  // True if all tests pass
  integration_complete?: boolean,  // True ONLY if tests pass AND coverage is comprehensive. Set to False if there are missing_test_cases or incomplete coverage, even if tests pass.
  failure_mode?: string | null,
  suggestions?: string[],
  files_to_check?: string[],
  test_output_summary?: string | null,
  passing?: number,
  failing?: number,
  skipped?: number,
  missing_test_cases?: ({
      name: string,  // Short name for the test case (e.g., 'tool_calling', 'multi_turn')
      description: string,  // What this test should verify
      priority?: string,  // Priority: high, medium, low
  })[],  // Test cases that should be added for better coverage
}
```

**CRITICAL**: Return valid JSON at the top level. Do NOT wrap in `{"output": ...}` or other root level keys.

## Turn Limit

You have **100 turns maximum**.

**Strategy:** Do NOT exhaustively explore. Work in phases: Quick scan -> Focused analysis -> Output.
Aim to complete in ~50 turns. If you hit the limit without output, the task fails.

## Environment

Your current working directory is: `.`

## Completion

Store concrete artifacts under `evidence/04/`, then update `PROGRESS.md` with
a bounded receipt containing the result, changed files, commands, and artifact paths. Keep raw
output under `evidence/04/raw/`; do not paste it into prompts or commit it. Do not advance
on failure.
