import os
import tomllib

updated = []

with open("pyproject.toml", "r") as f:
    projectlines = f.readlines()

with open("pyproject.toml", "rb") as f:
    loaded = tomllib.load(f)

    print(loaded)

for dep in loaded["project"]["dependencies"]:
    parts = dep.split(";")
    specs = parts[0].split(",")
    if ">" in specs[-1]:
        specs[-1] += "rc0"
    else:
        specs[-1] += "rc99"
    parts[0] = ",".join(specs)
    updated.append(";".join(parts))

print(updated)

new_output = []
replacing_index = -1
for line in projectlines:
    if len(updated) > replacing_index >= 0:
        new_output.append(f'    "{updated[replacing_index]}",\n')
        replacing_index += 1
    else:
        new_output.append(line)
    if line.startswith("dependencies = ["):
        replacing_index += 1

os.remove("pyproject.toml")
with open("pyproject.toml", "w") as f:
    f.writelines(new_output)
