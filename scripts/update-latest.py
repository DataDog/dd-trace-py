import subprocess


result = subprocess.run(["python", "scripts/freshvenvs.py"], capture_output=True, text=True)

pkgs = []
for line in result.stdout.splitlines():
    pkgs.append(line.split(":")[0])

for pkg in pkgs:
    print(pkg)
