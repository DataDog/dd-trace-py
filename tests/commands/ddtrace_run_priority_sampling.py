from ddtrace import config


if __name__ == "__main__":
    assert config._priority_sampling is True
    print("Test success")
