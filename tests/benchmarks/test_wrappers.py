def test_normal_wrap(benchmark):
    def func():
        pass

    benchmark(func)

