import os


if __name__ == "__main__":
    assert os.getenv("DD_SERVICE") == "my_test_service"
    print("Test success")
