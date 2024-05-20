import json
import sys

import requests


def send_incompatible_packages(data, url):
    headers = {"Content-Type": "application/json"}
    response = requests.post(url, data=json.dumps(data), headers=headers)
    if response.status_code == 200:
        print("Incompatible packages sent successfully.")
    else:
        print(f"Failed to send data. HTTP Status code: {response.status_code}")


if __name__ == "__main__":
    url = sys.argv[1]
    data = json.loads(sys.argv[2])
    send_incompatible_packages(data, url)
