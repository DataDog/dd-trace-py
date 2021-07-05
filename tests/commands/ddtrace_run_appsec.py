from ddtrace.appsec import mgmt


if __name__ == "__main__":
    if mgmt.protections:
        print("APPSEC LOADED")
    else:
        print("NOT LOADED")
