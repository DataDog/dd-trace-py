from threading import Thread
from threading import current_thread

from ddtrace.debugging._signal.log import get_thread_generation


def test_thread_generation_is_stable():
    thread = current_thread()
    assert get_thread_generation(thread) == get_thread_generation(thread)


def test_thread_generation_distinguishes_threads():
    generations = []

    def record():
        generations.append(get_thread_generation(current_thread()))

    for _ in range(3):
        t = Thread(target=record)
        t.start()
        t.join()

    # Each thread gets its own token, even though the OS is free to hand the
    # same ident to each of them in turn (which is precisely the ambiguity the
    # token exists to resolve).
    assert len(set(generations)) == len(generations), generations
    assert get_thread_generation(current_thread()) not in generations


def test_thread_generation_survives_ident_reuse():
    # Provoke the failure mode directly: run threads sequentially until two of
    # them report the same ident, then assert their generations still differ.
    seen: dict[int, int] = {}
    collisions = []

    def record():
        thread = current_thread()
        ident = thread.ident
        assert ident is not None
        generation = get_thread_generation(thread)
        if ident in seen:
            collisions.append((seen[ident], generation))
        seen[ident] = generation

    for _ in range(50):
        t = Thread(target=record)
        t.start()
        t.join()
        if collisions:
            break

    if not collisions:
        # Ident reuse is at the OS's discretion and not guaranteed to happen in
        # 50 attempts. Nothing to assert if it did not.
        return

    previous, current = collisions[0]
    assert previous != current
