#!/usr/bin/env bash
# Inline Experiments — end-to-end replay demo.
# Tells the dev-loop story: capture a known-good baseline, then replay the
# current code against it to tell a SAFE change apart from a REGRESSION.
#
# Uses the deterministic example so it runs with zero external deps. Point it at
# a real app by passing a different module:  ./demo.sh example_lux_shaped
set -e
cd "$(dirname "$0")"
PY="${PY:-python}"
APP="${1:-example_briefing_shaped}"

line() { printf '%s\n' "────────────────────────────────────────────────────────"; }

line; echo "  INLINE EXPERIMENTS — replay demo   (app: $APP)"; line; echo

echo "STEP 1 ▸ capture a baseline from the current, known-good code"
rm -f experiment_cases.jsonl
RUN_TS=t1 $PY experiment_poc.py capture "$APP:generate_traffic" >/dev/null
echo "  baseline recorded:"
sed 's/^/      /' experiment_cases.jsonl
echo

echo "STEP 2 ▸ developer reworks the summary wording — a SAFE refactor"
echo "         replay current code vs baseline (structural comparator)"
RUN_TS=t2 DRIFT=text $PY experiment_poc.py replay "$APP" --comparator structural
echo "  → MATCH: output shape preserved despite reworded text. Safe to ship. ✅"
echo

echo "STEP 3 ▸ a change accidentally DROPS a ticker — a REGRESSION"
echo "         replay current code vs baseline (structural comparator)"
RUN_TS=t2 DRIFT=drop $PY experiment_poc.py replay "$APP" --comparator structural
echo "  → CHANGED: output structure regressed. Caught before shipping. ❌"
echo
line; echo "  done — capture once, replay on every change."; line
