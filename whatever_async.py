#!/usr/bin/env python3
"""
Run with Python 3. Requires: pip install aiohttp
Creates 10 concurrent tasks using asyncio:
- 5 matrix multiplication tasks (pure Python, CPU-bound, in thread pool)
- 5 HTTP fetch tasks (aiohttp, I/O-bound, native async)
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import random
from time import perf_counter
from typing import List
from typing import Tuple

import aiohttp


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
    # A tiny reducer so we don't print whole matrices
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


async def fetch_url(url: str, session: aiohttp.ClientSession, timeout: float = 10.0) -> FetchResult:
    t0 = perf_counter()
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            data = await resp.read()
            elapsed = (perf_counter() - t0) * 1000
            return FetchResult(url, resp.status < 400, resp.status, len(data), elapsed)
    except aiohttp.ClientError as e:
        elapsed = (perf_counter() - t0) * 1000
        return FetchResult(url, False, -1, 0, elapsed, error=str(e))
    except asyncio.TimeoutError:
        elapsed = (perf_counter() - t0) * 1000
        return FetchResult(url, False, -1, 0, elapsed, error="Timeout")
    except Exception as e:
        elapsed = (perf_counter() - t0) * 1000
        return FetchResult(url, False, -1, 0, elapsed, error=str(e))


# ---------------- Workload orchestration ---------------- #


async def matrix_coro(
    name: str, a_rows: int, a_cols_b_rows: int, b_cols: int
) -> Tuple[str, int, int, int, float, float]:
    """Returns (name, m, k, n, checksum, elapsed_ms)."""
    elapsed_ms = 0.0
    C = [[]]
    while elapsed_ms < 5 * 1000:
        A = make_matrix(a_rows, a_cols_b_rows)
        B = make_matrix(a_cols_b_rows, b_cols)
        t0 = perf_counter()
        C = matmul(A, B)
        await asyncio.sleep(0.0)
        elapsed_ms += (perf_counter() - t0) * 1000.0

    return (name, a_rows, a_cols_b_rows, b_cols, checksum(C), elapsed_ms)


async def main(index: int) -> None:
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

    # Use ThreadPoolExecutor for CPU-bound operations only
    matrix_index = 0
    fetch_index = 0
    async with aiohttp.ClientSession() as session:
        with ThreadPoolExecutor(max_workers=5, thread_name_prefix="worker"):
            # Create tasks
            tasks: List[asyncio.Task] = []

            # Submit 5 matrix tasks (CPU-bound, in thread pool)
            for name, m, k, n in matrix_jobs:
                tasks.append(asyncio.create_task(matrix_coro(name, m, k, n), name=f"Matrix-{index}-{matrix_index}"))
                matrix_index += 1
            # Submit 5 fetch tasks (I/O-bound, native async)
            for url in urls:
                tasks.append(asyncio.create_task(fetch_url(url, session), name=f"Fetch-{index}-{fetch_index}"))
                fetch_index += 1

            # Collect results as they complete
            mm_times: List[float] = []
            fetch_times: List[float] = []

            for coro in asyncio.as_completed(tasks):
                try:
                    res = await coro
                except:
                    continue

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
    print(f"Total wall time with asyncio: {total_ms:.1f} ms")


if __name__ == "__main__":
    index = 0
    while True:
        try:
            asyncio.run(main(index))
            index += 1
        except (asyncio.CancelledError, Exception) as e:
            print(f"Error: {e}")
