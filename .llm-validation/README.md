# LLM validation

These CI checks evaluate coding-agent guidance with the shared
[LLM Validation Platform](https://github.com/ddoghq/llm-validation-platform/blob/v0.1.6/ci/README.md).
Results are advisory while the cases are calibrated.

## Pull request checks

`llm validation` compares answers before and after changes to the `instruction_files` listed in
[config.yaml](config.yaml). It skips `main` and skips answer evaluation when those files are unchanged.

When reviewing guidance, check correctness, duplication, and whether detailed procedures belong
in a skill or scoped documentation. Read the answers alongside the scores; a PASS does not prove
every instruction is useful.

## Monthly setup evaluation

`llm validation (setup smoke)` compares the declared repository setup against minimal guidance
at the same commit. It runs only when `LLMVAL_SETUP_ENABLED` is `true`; the monthly schedule also
selects the `setup-full` preset, which runs every setup-eligible case three times. Other pipelines
default to `setup-calibration`.

Setup artifacts are retained for 13 months. A result applies to the evaluated commit and tasks,
not to every individual skill. The cases use read-only answers and fixtures. They do not measure
full implementation workflows, multi-agent reviews, external MCPs/plugins, or alternative
model/effort settings.

## Where to find results

Open the CI job's artifacts:

| Check | Report | Answers and judgments |
|---|---|---|
| PR instructions | `report.md` | `details.json` |
| Monthly setup | `setup-report.md` | `setup-details.json` |

## Maintaining the checks

- Update [config.yaml](config.yaml): `instruction_files` controls PR comparisons; `setup` lists
  guidance removed from the minimal variant. Include new guidance and required resources.
- Add cases to [the suite](suites/python-tracer-agent-v0.1.yaml), verifying expected behavior
  against repository code and documentation. Use `comparisons: [setup]` for setup-only cases;
  `setup-full` automatically includes eligible cases. Keep a general Python control case.
- Keep both platform references in `.gitlab-ci.yml` in sync when upgrading.

For local execution and troubleshooting, see the pinned platform's
[runner guide](https://github.com/ddoghq/llm-validation-platform/blob/v0.1.6/README.md#run-locally).
