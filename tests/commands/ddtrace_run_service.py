import os


if __name__ == "__main__":
    assert os.getenv("DATADOG_SERVICE_NAME") == "my_test_service" or os.getenv("DD_SERVICE") == "my_test_service"
    print("Test success")
