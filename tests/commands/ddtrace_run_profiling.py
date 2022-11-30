from ddtrace.profiling import bootstrap


if __name__ == "__main__":
    if hasattr(bootstrap, "profiler"):
        print(str(bootstrap.profiler.status))
    else:
        print("NO PROFILER")
