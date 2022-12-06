from collections import defaultdict
from datetime import datetime
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
    """Simple HTML parser to parse a unique table"""

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
    """Build a Dataframe from the HTML report of mutmut"""
    with open(filename, "r") as htmlfile:
        parser = MyHTMLParser()
        parser.feed(htmlfile.read())
        D = defaultdict(list)
        for a, b, c, d, e in parser.table[1:]:
            D[(int(b), int(c), float(d))].append(a)
        return pd.DataFrame(
            [
                (
                    a,
                    b,
                    c,
                    d,
                    1 + math.log(b),
                    f"""<b>Coverage</b>: {d:4.1f}%<br><b>Mutants</b>: {b}<br>{'<br>'.join(sorted(a))}""",
                )
                for (b, c, d), a in D.items()
            ],
            columns=["filenames", "mutants", "killed", "coverage", "size", "text"],
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
    """
    Does a test file pass without error ?
    use a timeout of 15 seconds to avoid long tests
    """
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
    """Gather the lists of test files available"""
    test_files = []
    for file in os.scandir(dirname):
        if file.is_file() and file.name.endswith(".py"):  # and file.name != "__init__.py":
            test_files.append(file.path)
        elif file.is_dir():
            test_files.extend(find_all_local_test_files(file.path))
    return test_files


def find_all_local_passing_tests(dirname="tests"):
    """find all test file without any failing test using the local python provided"""
    # for some reason some tests may still fail after that with mutmut. Not sure why.
    test_files = find_all_local_test_files(dirname)
    print(f"Found {len(test_files)} test files")
    with Pool(8) as pool:
        all_tests = [t for res in pool.map(find_passing_test_file, test_files) for t in res]
    all_tests.sort()
    return " ".join(f for _, f in all_tests)


def find_all_local_src_files(dirname="ddtrace"):
    """Gather the lists of src files available in size order"""

    def rec_search(dirname):
        src_files = []
        for file in os.scandir(dirname):
            if file.is_file() and file.name.endswith(".py"):  # and file.name != "__init__.py":
                src_files.append(file.path)
            elif file.is_dir():
                src_files.extend(rec_search(file.path))
        return src_files

    return sorted(rec_search(dirname), key=lambda fn: os.stat(fn).st_size)


def tests_all_files():
    for file in find_all_local_src_files():
        size = os.stat(file).st_size
        if size > 250:
            res = input(f"{file} is large: {size}, press 'q' to quit")
            if res == "q":
                return
        print(f">> Mutation tests on {file}")
        run_io(["mutmut", "run", f"--paths-to-mutate={file}"])


def main():
    # run_io(["mutmut", "run", "--paths-to-mutate=ddtrace/tracer.py:ddtrace/appsec/iast"])
    print(">> Generating html")
    run_io(["mutmut", "html"])
    df = load_html_report("html/index.html")
    figure = df.plot(
        hover_data={
            "mutants": False,
            "coverage": False,
        },
        kind="scatter",
        x="mutants",
        y="coverage",
        size="size",
        color="coverage",
        log_x=True,
        custom_data=["text"],
        color_continuous_scale=[(0, "orange"), (0.3, "red"), (0.8, "blue"), (1, "green")],
    )  # hover_data=['petal_width']
    figure.update_traces(hovertemplate="%{customdata[0]}")
    figure.write_html(f"fig_{datetime.now().strftime('%Y_%m_%d_%H_%M_%S')}.html")


# main()
if __name__ == "__main__":
    # tests_all_files()
    # for file in find_all_local_src_files():
    #     print(file, os.stat(file).st_size)
    main()
