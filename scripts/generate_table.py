import csv
import json


print("Reading supported_versions_output.json")

with open("supported_versions_output.json", "r") as json_file:
    data = json.load(json_file)

columns = [
    "dependency",
    "integration",
    "minimum_tracer_tested",
    "maximum_tracer_tested",
    "minimum_tracer_supported",
    "maximum_tracer_supported",
    "auto-instrumented",
]
csv_rows = []

for entry in data:
    integration_name = entry.get("integration", "")
    dependency_name = entry.get("dependency", "")
    if entry.get("pinned", "").lower() == "true":
        integration_name += " *"
    entry["integration"] = integration_name
    entry["minimum_tracer_supported"] = entry.get("minimum_tracer_supported", "")
    entry["maximum_tracer_supported"] = entry.get("maximum_tracer_supported", "latest")
    csv_rows.append({col: entry.get(col, "") for col in columns})

with open("supported_versions_table.csv", "w", newline="") as csv_file:
    print("Wrote to supported_versions_table.csv")
    writer = csv.DictWriter(csv_file, fieldnames=columns)
    writer.writeheader()
    writer.writerows(csv_rows)
