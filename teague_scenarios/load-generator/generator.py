#!/usr/bin/env python

import requests



def generate(num_requests=1, url="http://test-service:5000"):
    import time
    client = requests.Session()
    for i in range(num_requests):
        response = client.get(url)
        print(response)
        time.sleep(0.1)




if __name__ == "__main__":
    generate(num_requests=1)