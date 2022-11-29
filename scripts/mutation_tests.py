import html.parser
import math
from multiprocessing import Pool
import os
from subprocess import DEVNULL
from subprocess import PIPE
from subprocess import TimeoutExpired
from subprocess import run
import sys
from time import CLOCK_THREAD_CPUTIME_ID
from time import clock_gettime_ns

import pandas as pd


# python -m pip install pandas plotly

pd.options.plotting.backend = "plotly"


def run_io(commands):
    return run(commands, stdout=sys.stdout, stderr=sys.stderr)


class MyHTMLParser(html.parser.HTMLParser):
    def __init__(self):
        html.parser.HTMLParser.__init__(self)
        self.table = []
        self.import_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.table.append([])
        elif tag in ["td", "th"]:
            self.import_cell = True

    def handle_endtag(self, tag):
        if tag in ["td", "th"]:
            self.import_cell = False

    def handle_data(self, data):
        if self.import_cell:
            self.table[-1].append(data)


def load_html_report(filename):
    with open(filename, "r") as htmlfile:
        parser = MyHTMLParser()
        parser.feed(htmlfile.read())
        return pd.DataFrame(
            [
                (a, int(b), int(c), int(e), float(d), 1 + math.log(int(b)), f"{float(d):4.1f}%")
                for a, b, c, d, e in parser.table[1:]
            ],
            columns=["filename", "mutants", "killed", "alive", "coverage", "size", "%Coverage"],
        )


def find_passing_tests(filename):
    """use a timeout of a few seconds to avoid long tests"""
    try:
        stdout = run(
            ["python", "-m", "pytest", "-r", "p", filename], stdout=PIPE, stderr=DEVNULL, timeout=10
        ).stdout.decode("utf-8")
    except TimeoutExpired:
        print(f"TIMEOUT for all tests in {filename}")
        return []
    passed_test = [line[7:] for line in stdout.split("\n") if line.startswith("PASSED ")]
    print(f"Found {len(passed_test):5} passing tests in {filename}")
    return passed_test


def find_passing_test_file(filename):
    """use a timeout of 15 seconds to avoid long tests"""
    try:
        time = clock_gettime_ns(CLOCK_THREAD_CPUTIME_ID)
        res = run(["python", "-m", "pytest", "-x", filename], stdout=DEVNULL, stderr=DEVNULL, timeout=15).returncode
        time = clock_gettime_ns(CLOCK_THREAD_CPUTIME_ID) - time
    except TimeoutExpired:
        print(f"TIMEOUT for all tests in {filename}")
        res = -1
    if res == 0:
        print(f"Found  ALL  passing tests in {filename} in {time/1_000_000:8.3f}ms")
        return [(time, filename)]
    else:
        return []


def find_all_local_test_files(dirname="tests"):
    test_files = []
    for file in os.scandir(dirname):
        if file.is_file() and file.name.endswith(".py"):  # and file.name != "__init__.py":
            test_files.append(file.path)
        elif file.is_dir():
            test_files.extend(find_all_local_test_files(file.path))
    return test_files


def find_all_local_passing_tests(dirname="tests"):
    test_files = find_all_local_test_files(dirname)
    print(f"Found {len(test_files)} test files")
    with Pool(8) as pool:
        all_tests = [t for res in pool.map(find_passing_test_file, test_files) for t in res]
    all_tests.sort()
    return " ".join(f for _, f in all_tests)


def main():
    # run_io(["mutmut", "run", "--paths-to-mutate=ddtrace/tracer.py:ddtrace/appsec/iast"])
    # print(">> Generating html")
    # run_io(["mutmut", "html"])
    df = load_html_report("html/index.html")
    figure = df.plot(
        hover_data={"filename": True, "size": False, "mutants": True, "%Coverage": True, "coverage": False},
        kind="scatter",
        x="mutants",
        y="coverage",
        size="size",
        color="coverage",
        log_x=True,
        color_continuous_scale=[(0, "orange"), (0.3, "red"), (0.8, "blue"), (1, "green")],
    )  # hover_data=['petal_width']
    figure.write_html("fig.html")


# main()
if __name__ == "__main__":
    print(find_all_local_passing_tests())
