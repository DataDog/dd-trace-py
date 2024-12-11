from concurrent.futures import ThreadPoolExecutor
import http
import json
import multiprocessing
import os
import subprocess
import time

from ddtrace.vendor import psutil


# Port on which the sidecar server will run
SIDECAR_PORT = os.getenv("DD_TRACE_SIDECAR_PORT", "8129")
# Host on which the sidecar server will run
# Using localhost here prevents outside connections to the sidecar server.
SIDECAR_HOST = "127.0.0.1"

# Create a ThreadPoolExecutor with a maximum of 10 workers
executor = ThreadPoolExecutor(max_workers=100)


def kill_process_on_port(port):
    for proc in psutil.process_iter(attrs=["pid", "name", "connections"]):
        try:
            if not proc.info["connections"]:
                continue
            # Iterate through the connections of each process
            for conn in proc.info["connections"]:
                # Check if the connection is in the listening state and on the desired port
                if conn.status == "LISTEN" and conn.laddr.port == int(port):
                    print(f"Killing process {proc.info['name']} with PID {proc.info['pid']} on port {port}")
                    proc.terminate()  # Terminate the process
                    proc.wait()  # Wait for the process to terminate
                    return
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue


# Function to start the HTTP server
def start_sidecar_server():
    # The command to start a simple HTTP server on port 8000
    kill_process_on_port(SIDECAR_PORT)
    subprocess.run(["python3", "-m", "ddtrace.ddsidecar", SIDECAR_PORT])


def run_tracing_sidecar():
    server_process = multiprocessing.Process(target=start_sidecar_server)
    server_process.start()

    # Give the server some time to start up
    time.sleep(1)
    assert is_sidecar_alive(), "sidecar failed to start"


def is_sidecar_alive():
    # Send a GET request to the root endpoint
    conn = http.client.HTTPConnection("localhost", SIDECAR_PORT)
    conn.request("POST", "/alive")
    response = conn.getresponse()
    return response.status == 200


# Function to send the request (for use in ThreadPoolExecutor)
def send_request_threadpool(path, body):
    # Create a connection to the local server
    conn = http.client.HTTPConnection("localhost", SIDECAR_PORT)

    # Send a POST request to the path with the body
    conn.request("POST", path, body=json.dumps(body))

    # Get the response
    response = conn.getresponse()
    response_str = response.read().decode("utf-8")

    # Print the response status and body for debugging
    assert response.status == 200, f"Response Status: {response.status}, Response Body: {response_str}"
    conn.close()


def update_sidecar(path, body):
    # Create a connection to the local server
    # Submit the request function for asynchronous execution
    executor.submit(send_request_threadpool, path, body)


# Syncronous version of the function above
# def update_sidecar(path, body):
#     # Create a connection to the local server
#     conn = http.client.HTTPConnection("localhost", SIDECAR_PORT)
#     # Send a GET request to the root endpoint
#     conn.request("POST", path, body=json.dumps(body))
#     # Get the response
#     response = conn.getresponse()
#     response_str = response.read().decode("utf-8")
#     # Print the response status and body
#     assert response.status == 200, f"Response Status: {response.status}, Response Body: {response_str}"
#     conn.close()
#     if response_str:
#         return json.loads(response_str)
#     return {}


## Benchmark script
## Run with DD_TRACE_LOW_CPU_MODE=false python ... and DD_TRACE_LOW_CPU_MODE=true python ...

# import timeit
# from ddtrace import tracer


# def setup_create_span():
#     return tracer.trace(name="spanname", resource="blah", service="myapp", span_type="web")

# def create_and_finish_span():
#     span = tracer.trace(name="spanname", resource="blah", service="myapp", span_type="web")
#     span.finish()

# # Helper function to time the functions
# def time_function(func, setup, number=100):
#     return timeit.timeit(func, globals=globals(), setup=setup, number=number)

# if __name__ == "__main__":
#     # Setup code to create the span before each benchmark
#     setup_code = "span = setup_create_span()"

#     # Running the benchmarks
#     print("Benchmark Results:")
#     print(f"Create Span: {time_function('create_and_finish_span()', ''):.6f} seconds")
#     print(f"Set Tag: {time_function('span.set_tag("k", "v")', setup_code):.6f} seconds")
#     print(f"Set Metric: {time_function('span.set_metric("k", 1)', setup_code):.6f} seconds")
#     print(f"Set Link: {time_function('span.set_link(trace_id=20, span_id=10)', setup_code):.6f} seconds")
#     print(f"Add Event: {time_function('span._add_event(name="hi")', setup_code):.6f} seconds")
#     print(f"Set Resource: {time_function('span.resource = 1', setup_code):.6f} seconds")
#     print(f"Set Service: {time_function('span.service = 2', setup_code):.6f} seconds")
#     print(f"Set Span Type: {time_function('span.span_type = "web"', setup_code):.6f} seconds")
#     print(f"Set Start NS: {time_function('span.start_ns = 10000', setup_code):.6f} seconds")
#     print(f"Set Duration NS: {time_function('span.duration_ns = 10000', setup_code):.6f} seconds")
