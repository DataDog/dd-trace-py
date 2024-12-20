from ddtrace.opentracer import Tracer as OTTracer


if __name__ == "__main__":
    tracer = OTTracer()
    print(tracer._service_name)
