# Codex review resolution replies for PR #20365

Post the corresponding reply in each resolved conversation, then resolve it.
All eight fixes are in [2c9a953](https://github.com/DataDog/dd-trace-py/commit/2c9a9533bc3e4fffd04ac30759fd9955dd850864).

## Raise the supported Temporal version floor

<https://github.com/DataDog/dd-trace-py/pull/20365#discussion_r4025977896>

Addressed in [2c9a953](https://github.com/DataDog/dd-trace-py/commit/2c9a9533bc3e4fffd04ac30759fd9955dd850864).

## Apply the Temporal service configuration automatically

<https://github.com/DataDog/dd-trace-py/pull/20365#discussion_r4025977907>

Addressed in [2c9a953](https://github.com/DataDog/dd-trace-py/commit/2c9a9533bc3e4fffd04ac30759fd9955dd850864).

## Preserve deterministic trace IDs with explicit tracers

<https://github.com/DataDog/dd-trace-py/pull/20365#discussion_r4025977912>

Addressed in [2c9a953](https://github.com/DataDog/dd-trace-py/commit/2c9a9533bc3e4fffd04ac30759fd9955dd850864).

## Use the update name for update-with-start spans

<https://github.com/DataDog/dd-trace-py/pull/20365#discussion_r4025977931>

Addressed in [2c9a953](https://github.com/DataDog/dd-trace-py/commit/2c9a9533bc3e4fffd04ac30759fd9955dd850864).

## Tag generated spans with the Temporal component

<https://github.com/DataDog/dd-trace-py/pull/20365#discussion_r4025977939>

Addressed in [2c9a953](https://github.com/DataDog/dd-trace-py/commit/2c9a9533bc3e4fffd04ac30759fd9955dd850864).

## Record the namespace on workflow spans

<https://github.com/DataDog/dd-trace-py/pull/20365#discussion_r4025977949>

Addressed in [2c9a953](https://github.com/DataDog/dd-trace-py/commit/2c9a9533bc3e4fffd04ac30759fd9955dd850864).

## Isolate tracing callback failures from Temporal operations

<https://github.com/DataDog/dd-trace-py/pull/20365#discussion_r4025977963>

Addressed in [2c9a953](https://github.com/DataDog/dd-trace-py/commit/2c9a9533bc3e4fffd04ac30759fd9955dd850864).

## Allocate a workflow config per interceptor

<https://github.com/DataDog/dd-trace-py/pull/20365#discussion_r4025977974>

Addressed in [2c9a953](https://github.com/DataDog/dd-trace-py/commit/2c9a9533bc3e4fffd04ac30759fd9955dd850864).

## Intentionally left open

The "Flush child spans before long-running workflows complete" conversation remains open. The follow-up commit documents a process-wide partial-flush configuration workaround, but does not change the integration's default behavior.
