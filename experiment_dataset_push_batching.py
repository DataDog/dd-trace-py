# Test that Dataset.push() correctly batches large payloads internally.
#
# 1. Seed a dataset with 10k small records via bulk_upload
# 2. Locally: delete 5k, update remaining 5k to ~2KB each, add 5k new ~2KB records
# 3. Push with bulk_upload=False — should be ~20MB total and exercise all three op types
#
# Usage:  dd-auth --domain dd.datad0g.com -- python experiment_dataset_push_batching.py
import logging
import os
import time
from datetime import datetime, timezone

from ddtrace.llmobs import LLMObs


logging.basicConfig(level=logging.WARNING)
logging.getLogger("ddtrace.llmobs._experiment").setLevel(logging.DEBUG)

LLMObs.enable(
    api_key=os.getenv("DD_API_KEY"),
    app_key=os.getenv("DD_APP_KEY"),
    site=os.getenv("DD_SITE"),
    project_name="christopher_fox_stress_test",
    agentless_enabled=True,
)

RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
SEED_COUNT = 10_000
RECORD_BYTES_LARGE = 2_000  # ~2KB per record for updates/inserts
_PULL_DELAY = 30

# ---------------------------------------------------------------------------
# Step 1: Seed dataset with 10k small records
# ---------------------------------------------------------------------------
ds_name = f"mixed-batch-test-{RUN_ID}"
print(f"Creating dataset: {ds_name}")
ds = LLMObs.create_dataset(dataset_name=ds_name, description="Mixed ops batching test")

seed_records = [
    {
        "input_data": {"text": f"seed-{i}"},
        "expected_output": f"output-{i}",
        "metadata": {"idx": i},
    }
    for i in range(SEED_COUNT)
]

SEED_BATCH = 1_000
print(f"Seeding with {SEED_COUNT} small records in batches of {SEED_BATCH}...")
t0 = time.perf_counter()
for batch_start in range(0, SEED_COUNT, SEED_BATCH):
    batch_end = min(batch_start + SEED_BATCH, SEED_COUNT)
    ds.extend(seed_records[batch_start:batch_end])
    ds.push(bulk_upload=False)
    print(f"  pushed records {batch_start}..{batch_end - 1}")
print(f"Seed push completed in {time.perf_counter() - t0:.1f}s")

print(f"Waiting {_PULL_DELAY}s for server to index...")
time.sleep(_PULL_DELAY)

ds = LLMObs.pull_dataset(dataset_name=ds.name)
print(f"Pulled seeded dataset: {len(ds)} records")

# ---------------------------------------------------------------------------
# Step 2: Delete 5k, update 5k to ~2KB, add 5k new ~2KB records
# ---------------------------------------------------------------------------
NUM_DELETE = 5_000
NUM_UPDATE = 5_000  # the remaining 5k
NUM_INSERT = 5_000

# Delete the last 5k records (from end so indices stay stable)
print(f"Deleting {NUM_DELETE} records (indices {len(ds) - NUM_DELETE}..{len(ds) - 1})...")
for _ in range(NUM_DELETE):
    ds.delete(len(ds) - 1)

# Update the remaining 5k records to ~2KB each
print(f"Updating {NUM_UPDATE} records to ~2KB each...")
for i in range(NUM_UPDATE):
    ds.update(i, {"input_data": {"text": "u" * RECORD_BYTES_LARGE}, "metadata": {"idx": i, "updated": True}})

# Add 5k new ~2KB records
print(f"Adding {NUM_INSERT} new ~2KB records...")
new_records = [
    {
        "input_data": {"text": "n" * RECORD_BYTES_LARGE},
        "expected_output": f"new-output-{i}",
        "metadata": {"idx": SEED_COUNT + i, "new": True},
    }
    for i in range(NUM_INSERT)
]
ds.extend(new_records)

expected_final = SEED_COUNT - NUM_DELETE + NUM_INSERT  # 10000

# ---------------------------------------------------------------------------
# Step 3: Push with batch update (should be ~20MB, exercises all 3 op types)
# ---------------------------------------------------------------------------
print(f"\nPushing mixed ops (inserts={NUM_INSERT}, updates={NUM_UPDATE}, deletes={NUM_DELETE}) with bulk_upload=False...")
t0 = time.perf_counter()
ds.push(bulk_upload=False)
push_elapsed = time.perf_counter() - t0
print(f"ds.push() completed in {push_elapsed:.1f}s")

print(f"Waiting {_PULL_DELAY}s for server to index...")
time.sleep(_PULL_DELAY)

t0 = time.perf_counter()
ds_pulled = LLMObs.pull_dataset(dataset_name=ds.name)
pull_elapsed = time.perf_counter() - t0
print(f"LLMObs.pull_dataset() {pull_elapsed:.1f}s")

num_updated = sum(1 for r in ds_pulled if r.get("metadata", {}).get("updated"))
num_new = sum(1 for r in ds_pulled if r.get("metadata", {}).get("new"))

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print(f"\n--- Summary ---")
print(f"Expected records: {expected_final}")
print(f"Pulled records:   {len(ds_pulled)}")
print(f"Updated records:  {num_updated}/{NUM_UPDATE}")
print(f"New records:      {num_new}/{NUM_INSERT}")
