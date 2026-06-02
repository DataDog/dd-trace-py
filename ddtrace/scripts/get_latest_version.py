import sys

import requests


def get_latest_version(package_name):
    response = requests.get(f"https://pypi.org/pypi/{package_name}/json", timeout=10)
    data = response.json()
    return data["info"]["version"]


package_name = sys.argv[1]
latest_version = get_latest_version(package_name)
print(f"{latest_version}")
