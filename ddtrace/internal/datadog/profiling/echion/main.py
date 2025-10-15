import asyncio
import time


def matmul():
    a = [[1 for _ in range(100)] for _ in range(100)]
    b = [[1 for _ in range(100)] for _ in range(100)]
    c = [[0 for _ in range(100)] for _ in range(100)]
    for i in range(100):
        for j in range(100):
            for k in range(100):
                c[i][j] += a[i][k] * b[k][j]

def do_nothing():
    time.sleep(0.01)

async def my_function() -> None:
    for _ in range(100):
        matmul()
        print(f"Matmul done {_}")  # noqa: T201

def my_function_2():
    start=time.time()
    while time.time() - start < 15:
        do_nothing()

def main():
    start = time.time()
    asyncio.run(my_function())
    end = time.time()
    print(f"Time taken: {end - start} seconds")  # noqa: T201
    with open("time.txt", "a") as f:
        f.write(f"{end - start}\n")

if __name__ == "__main__":
    # main()

    my_function_2()

