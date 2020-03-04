"""A test application that is profiled."""
import logging
import sys

logging.basicConfig(stream=sys.stderr, level=logging.INFO)

import ddtrace.profiling.auto  # noqa

# Sudoku generator and solver
# Source: https://github.com/MorvanZhou/sudoku
import numpy as np  # noqa


def generate_sudoku(mask_rate=0.5):
    while True:
        n = 9
        m = np.zeros((n, n), np.int)
        rg = np.arange(1, n + 1)
        m[0, :] = np.random.choice(rg, n, replace=False)
        try:
            for r in range(1, n):
                for c in range(n):
                    col_rest = np.setdiff1d(rg, m[:r, c])
                    row_rest = np.setdiff1d(rg, m[r, :c])
                    avb1 = np.intersect1d(col_rest, row_rest)
                    sub_r, sub_c = r // 3, c // 3
                    avb2 = np.setdiff1d(
                        np.arange(0, n + 1), m[sub_r * 3 : (sub_r + 1) * 3, sub_c * 3 : (sub_c + 1) * 3].ravel()
                    )
                    avb = np.intersect1d(avb1, avb2)
                    m[r, c] = np.random.choice(avb, size=1)
            break
        except ValueError:
            pass
    mm = m.copy()
    mm[np.random.choice([True, False], size=m.shape, p=[mask_rate, 1 - mask_rate])] = 0
    return mm


def solve(m):
    if isinstance(m, list):
        m = np.array(m)
    elif isinstance(m, str):
        m = np.loadtxt(m, dtype=np.int, delimiter=",")
    rg = np.arange(m.shape[0] + 1)
    while True:
        mt = m.copy()
        while True:
            d = []
            d_len = []
            for i in range(m.shape[0]):
                for j in range(m.shape[1]):
                    if mt[i, j] == 0:
                        possibles = np.setdiff1d(
                            rg,
                            np.union1d(
                                np.union1d(mt[i, :], mt[:, j]),
                                mt[3 * (i // 3) : 3 * (i // 3 + 1), 3 * (j // 3) : 3 * (j // 3 + 1)],
                            ),
                        )
                        d.append([i, j, possibles])
                        d_len.append(len(possibles))
            if len(d) == 0:
                break
            idx = np.argmin(d_len)
            i, j, p = d[idx]
            if len(p) > 0:
                num = np.random.choice(p)
            else:
                break
            mt[i, j] = num
            if len(d) == 0:
                break
        if np.all(mt != 0):
            break

    return mt


def check_solution(m):
    if isinstance(m, list):
        m = np.array(m)
    elif isinstance(m, str):
        m = np.loadtxt(m, dtype=np.int, delimiter=",")
    set_rg = set(np.arange(1, m.shape[0] + 1))
    no_good = False
    for i in range(m.shape[0]):
        for j in range(m.shape[1]):
            r1 = set(m[3 * (i // 3) : 3 * (i // 3 + 1), 3 * (j // 3) : 3 * (j // 3 + 1)].ravel()) == set_rg
            r2 = set(m[i, :]) == set_rg
            r3 = set(m[:, j]) == set_rg
            if not (r1 and r2 and r3):
                no_good = True
                break
        if no_good:
            break


if __name__ == "__main__":
    for _ in range(10):
        puzzle = generate_sudoku(mask_rate=0.5)
        solved = solve(puzzle)
        check_solution(solved)
    print("Solved!")
