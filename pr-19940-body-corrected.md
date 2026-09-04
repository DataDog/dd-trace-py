https://github.com/DataDog/dd-trace-py/pull/19937: < Prev PR

## Description

[#19937](https://github.com/DataDog/dd-trace-py/pull/19937) already ships runtime GC DogStatsD. This PR adds an opt-in profiling collector when `DD_PROFILING_GC_ENABLED=true` (default false). `GCCollector` subscribes to the shared `GCPauseMonitor` — one `gc.callbacks` entry with runtime metrics — and emits `push_gctime` on `gc.collect[gen=N]` from `snapshot()` only, plus a `gc.config` sample with explicit `gc.collect()` tally via `push_walltime`. No `push_alloc`.

| Layer | Meaning | Which PR in the stack? |
| --- | --- | --- |
| Compiled into artifact | Cargo features, USDT notes, binary contents | n/a (Python) |
| Armed at runtime | install()/hooks actually take effect | this PR, flag on |
| Observable in product/Python | capability ABI, logs, config, reno | this PR |

## Testing

Unit/integration tests pin default-off, `push_gctime` for pause samples, `push_walltime` only on `gc.config`, no `push_alloc`, shared-monitor refcount, and `SampleHandle` only from `snapshot()`.

## Risks

Hooks `gc.callbacks` on the STW path; a deadlock class was fixed in CI. Default-off. Staging soaks (`gc19940_smk`, `gc19940_aig`) graded overall regression with validity unknown — not perf proof. Smoke had +51.7% pod_restarts and contaminated process; ai_gateway had ~+64% traffic imbalance.

## Additional Notes

B: #19937 runtime DogStatsD · E: this PR opt-in profile samples. CompView observer frames ~0.01% eBPF CPU as an overhead ceiling. FH+eBPF is a follow-up spike gated on CPython symbol availability; not in scope.
