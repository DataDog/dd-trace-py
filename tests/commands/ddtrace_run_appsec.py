from ddtrace.appsec import _mgmt


if __name__ == "__main__":
    if _mgmt.enabled:
        print("APPSEC LOADED")
    else:
        print("NOT LOADED")
