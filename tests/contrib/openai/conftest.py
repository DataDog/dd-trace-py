def pytest_configure(config):
    config.addinivalue_line("markers", "vcr_logs(*args, **kwargs): mark test to have logs requests recorded")
