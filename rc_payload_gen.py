import json
import time


IP = "123.45.67.88"

rc_config_create = {
    "rules_data": [
        {
            "data": [
                {"expiration": int(time.time()) + 1000000, "value": IP},
            ],
            "id": "blocked_ips",
            "type": "ip_with_expiration",
        },
    ]
}
data_create = {"path": "datadog/2/ASM_DATA/blocked_users/config", "msg": rc_config_create}
rc_config_del = {"rules_data": []}
data_del = {"path": "datadog/2/ASM_DATA/blocked_users/config", "msg": rc_config_del}

print("curl -X POST 'http://0.0.0.0:8126/test/session/responses/config/path' -d '{}'".format(json.dumps(data_create)))
print("curl -X POST 'http://0.0.0.0:8126/test/session/responses/config/path' -d '{}'".format(json.dumps(data_del)))
print("curl -X POST 'http://0.0.0.0:8126/test/session/responses/config' -d '{}'")

print("curl -X POST 'http://0.0.0.0:8126/v0.7/config'")
print("curl -X POST 'http://0.0.0.0:8126/v0.7/config'")
print(
    "curl --request GET 'http://127.0.0.1:8000/block' --header 'X-Forwarded-For: {}' -A 'dd-test-scanner-log'".format(
        IP
    )
)
