import json


with open("supported_versions.json", "r") as json_file:
    data = json.load(json_file)

columns = [
    "integration",
    "minimum_tracer_supported",
    "max_tracer_supported",
    "minumum_available_supported",
    "maximum_available_supported",
]

column_width = 30  # set a fixed width for each column


def format_cell(content, width):
    return f"{content:<{width}}"


markdown_content = "| " + " | ".join(format_cell(col, column_width) for col in columns) + " |\n"
markdown_content += "| " + " | ".join(["-" * column_width] * len(columns)) + " |\n"

for entry in data:
    row = [format_cell(entry.get(col, ""), column_width) for col in columns]
    markdown_content += "| " + " | ".join(row) + " |\n"

with open("supported_versions_table.md", "w") as md_file:
    md_file.write(markdown_content)
