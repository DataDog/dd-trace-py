#!/usr/bin/env python3
"""
Run with Python 3. No third-party deps.
Creates 10 threads total:
- 5 matrix multiplication tasks (pure Python)
- 5 HTTP fetch tasks (standard library urllib)
"""

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from dataclasses import dataclass
import random
from time import perf_counter
from typing import List
from typing import Tuple
import urllib.error
import urllib.request


# ---------------- Matrix utilities (pure Python) ---------------- #

Matrix = List[List[float]]


def make_matrix(rows: int, cols: int, lo: float = -10.0, hi: float = 10.0) -> Matrix:
    rng = random.Random()  # local RNG
    return [[rng.uniform(lo, hi) for _ in range(cols)] for _ in range(rows)]


def matmul(A: Matrix, B: Matrix) -> Matrix:
    # Basic triple-loop matrix multiply, O(n^3), no numpy.
    if not A or not B or len(A[0]) != len(B):
        raise ValueError(
            "Incompatible shapes for multiplication: "
            f"{len(A)}x{len(A[0]) if A else 0} * {len(B)}x{len(B[0]) if B else 0}"
        )
    m, k, n = len(A), len(B), len(B[0])
    # Precompute transpose of B for better cache locality
    Bt = [[B[r][c] for r in range(k)] for c in range(n)]
    C = [[0.0] * n for _ in range(m)]
    for i in range(m):
        Ai = A[i]
        Ci = C[i]
        for j in range(n):
            btj = Bt[j]
            # dot product of Ai and B column j
            s = 0.0
            for t in range(k):
                s += Ai[t] * btj[t]
            Ci[j] = s
    return C


def checksum(M: Matrix) -> float:
    # A tiny reducer so we don’t print whole matrices
    return sum(sum(row) for row in M)


# ---------------- HTTP fetch (standard library) ---------------- #


@dataclass
class FetchResult:
    url: str
    ok: bool
    status: int
    size: int
    elapsed_ms: float
    error: str = ""


def fetch_url(url: str, timeout: float = 10.0) -> FetchResult:
    t0 = perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = resp.read()
            elapsed = (perf_counter() - t0) * 1000
            status = getattr(resp, "status", 200)  # Py3.8+: HTTPResponse.status
            return FetchResult(url, True, status, len(data), elapsed)
    except urllib.error.HTTPError as e:
        elapsed = (perf_counter() - t0) * 1000
        return FetchResult(url, False, e.code, 0, elapsed, error=str(e))
    except Exception as e:
        elapsed = (perf_counter() - t0) * 1000
        return FetchResult(url, False, -1, 0, elapsed, error=str(e))


# ---------------- Workload orchestration ---------------- #


def matrix_task(name: str, a_rows: int, a_cols_b_rows: int, b_cols: int) -> Tuple[str, int, int, int, float, float]:
    """Returns (name, m, k, n, checksum, elapsed_ms)."""
    A = make_matrix(a_rows, a_cols_b_rows)
    B = make_matrix(a_cols_b_rows, b_cols)
    t0 = perf_counter()
    C = matmul(A, B)
    elapsed_ms = (perf_counter() - t0) * 1000.0
    return (name, a_rows, a_cols_b_rows, b_cols, checksum(C), elapsed_ms)


def main():
    # You can tweak sizes/URLs as you like. Sizes are modest but non-trivial.
    matrix_jobs = [
        ("MM-1", 120, 120, 120),
        ("MM-2", 100, 120, 80),
        ("MM-3", 96, 128, 64),
        ("MM-4", 80, 100, 90),
        ("MM-5", 64, 96, 128),
    ]
    urls = [
        "https://example.com/",
        "https://httpbin.org/get",
        "https://httpbin.org/bytes/2048",
        "https://www.iana.org/domains/reserved",
        "https://httpbin.org/delay/1",  # small artificial delay
    ]

    t_start = perf_counter()
    futures = []
    with ThreadPoolExecutor(max_workers=10, thread_name_prefix="worker") as ex:
        # Submit 5 matrix tasks
        for name, m, k, n in matrix_jobs:
            futures.append(ex.submit(matrix_task, name, m, k, n))
        # Submit 5 fetch tasks
        for url in urls:
            futures.append(ex.submit(fetch_url, url))

        # Collect as they finish
        done_count = 0
        mm_times = []
        fetch_times = []
        for fut in as_completed(futures):
            res = fut.result()
            done_count += 1
            if isinstance(res, tuple):
                # Matrix result
                name, m, k, n, cs, ms = res
                mm_times.append(ms)
                print(f"[{name}] {m}x{k} * {k}x{n} -> checksum={cs:.3f} in {ms:.1f} ms")
            elif isinstance(res, FetchResult):
                fetch_times.append(res.elapsed_ms)
                status_txt = f"{res.status}" if res.ok else f"ERR({res.status})"
                size_txt = f"{res.size} B"
                extra = "" if res.ok else f" // {res.error}"
                print(f"[FETCH] {res.url} -> {status_txt}, {size_txt} in {res.elapsed_ms:.1f} ms{extra}")
            else:
                print("[WARN] Unknown result:", res)

    total_ms = (perf_counter() - t_start) * 1000.0
    # Small summary
    if mm_times:
        avg_mm = sum(mm_times) / len(mm_times)
        print(f"\nMatrix tasks: {len(mm_times)} completed (avg {avg_mm:.1f} ms each).")
    if fetch_times:
        avg_fetch = sum(fetch_times) / len(fetch_times)
        print(f"Fetch tasks: {len(fetch_times)} completed (avg {avg_fetch:.1f} ms each).")
    print(f"Total wall time with 10 threads: {total_ms:.1f} ms")


if __name__ == "__main__":
    while True:
        main()
