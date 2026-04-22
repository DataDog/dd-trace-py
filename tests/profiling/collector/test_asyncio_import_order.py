import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_start_profiler_from_process_before_importing_asyncio",
    ),
    err=None,
)
# For macOS: err=None ignores expected stderr from tracer failing to connect to agent (not relevant to this test)
def test_asyncio_start_profiler_from_process_before_importing_asyncio() -> None:
    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    assert stack.is_available, stack.failure_msg

    p = profiler.Profiler()
    p.start()

    import asyncio
    import os
    import sys
    import time

    from tests.profiling.collector import pprof_utils

    # Start an asyncio loop BEFORE importing profiler modules
    # This simulates the bug scenario where a loop exists before profiling is enabled
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def my_function():
        async def background_task_func() -> None:
            """Background task that runs in the existing loop."""
            await asyncio.sleep(1.5)

        # Create and start a task in the existing loop
        background_task = loop.create_task(background_task_func(), name="background")
        assert background_task is not None

        # Run tasks that should be tracked
        sleep_time = 0.2
        loop_run_time = 0.75

        async def tracked_task() -> None:
            start_time = time.time()
            while time.time() < start_time + loop_run_time:
                await asyncio.sleep(sleep_time)

        async def main_task():
            t1 = asyncio.create_task(tracked_task(), name="tracked 1")
            t2 = asyncio.create_task(tracked_task(), name="tracked 2")
            await tracked_task()
            await asyncio.sleep(0.25)
            return t1, t2

        result = await main_task()

        await background_task

        return tracked_task, background_task_func, result

    main_task = loop.create_task(my_function(), name="main")
    tracked_task_def, background_task_def, (t1, t2) = loop.run_until_complete(main_task)

    p.stop()

    t1_name = t1.get_name()
    t2_name = t2.get_name()

    assert t1_name == "tracked 1"
    assert t2_name == "tracked 2"

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)

    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(samples) > 0, "No task names found - existing loop was not tracked!"

    if sys.version_info >= (3, 11):
        EXPECTED_FUNCTION_NAME_BACKGROUND = f"{my_function.__name__}.<locals>.{background_task_def.__name__}"
    else:
        EXPECTED_FUNCTION_NAME_BACKGROUND = background_task_def.__name__

    EXPECTED_FILENAME_BACKGROUND = os.path.basename(background_task_def.__code__.co_filename)
    EXPECTED_LINE_NO_BACKGROUND = -1  # any line

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name="background",
            locations=[
                pprof_utils.StackLocation(
                    function_name=EXPECTED_FUNCTION_NAME_BACKGROUND,
                    filename=EXPECTED_FILENAME_BACKGROUND,
                    line_no=EXPECTED_LINE_NO_BACKGROUND,
                ),
            ],
        ),
        print_samples_on_failure=True,
    )

    # Verify specific tasks are in the profile
    if sys.version_info >= (3, 11):
        EXPECTED_FUNCTION_NAME_TRACKED = f"{my_function.__name__}.<locals>.{tracked_task_def.__name__}"
    else:
        EXPECTED_FUNCTION_NAME_TRACKED = tracked_task_def.__name__

    EXPECTED_FILENAME_TRACKED = os.path.basename(tracked_task_def.__code__.co_filename)

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name=t1_name,
            locations=[
                pprof_utils.StackLocation(
                    function_name=EXPECTED_FUNCTION_NAME_TRACKED,
                    filename=EXPECTED_FILENAME_TRACKED,
                    line_no=-1,  # any line
                )
            ],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_start_profiler_from_process_before_starting_loop",
    ),
    err=None,
)
# For macOS: err=None ignores expected stderr from tracer failing to connect to agent (not relevant to this test)
def test_asyncio_start_profiler_from_process_before_starting_loop() -> None:
    import asyncio
    import os
    import sys
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    assert stack.is_available, stack.failure_msg

    p = profiler.Profiler()
    p.start()

    # Start an asyncio loop BEFORE importing profiler modules
    # This simulates the bug scenario where a loop exists before profiling is enabled
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def my_function():
        async def background_task_func() -> None:
            """Background task that runs in the existing loop."""
            await asyncio.sleep(1.5)

        # Create and start a task in the existing loop
        background_task = loop.create_task(background_task_func(), name="background")
        assert background_task is not None

        # Run tasks that should be tracked
        sleep_time = 0.2
        loop_run_time = 0.75

        async def tracked_task() -> None:
            start_time = time.time()
            while time.time() < start_time + loop_run_time:
                await asyncio.sleep(sleep_time)

        async def main_task():
            t1 = asyncio.create_task(tracked_task(), name="tracked 1")
            t2 = asyncio.create_task(tracked_task(), name="tracked 2")
            await tracked_task()
            await asyncio.sleep(0.25)
            return t1, t2

        result = await main_task()

        await background_task

        return tracked_task, background_task_func, result

    main_task = loop.create_task(my_function(), name="main")
    tracked_task_def, background_task_def, (t1, t2) = loop.run_until_complete(main_task)

    p.stop()

    t1_name = t1.get_name()
    t2_name = t2.get_name()

    assert t1_name == "tracked 1"
    assert t2_name == "tracked 2"

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)

    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(samples) > 0, "No task names found - existing loop was not tracked!"

    if sys.version_info >= (3, 11):
        EXPECTED_FUNCTION_NAME_BACKGROUND = f"{my_function.__name__}.<locals>.{background_task_def.__name__}"
    else:
        EXPECTED_FUNCTION_NAME_BACKGROUND = background_task_def.__name__
    EXPECTED_FILENAME_BACKGROUND = os.path.basename(background_task_def.__code__.co_filename)
    EXPECTED_LINE_NO_BACKGROUND = -1  # any line

    # Verify specific tasks are in the profile
    if sys.version_info >= (3, 11):
        EXPECTED_FUNCTION_NAME_TRACKED = f"{my_function.__name__}.<locals>.{tracked_task_def.__name__}"
    else:
        EXPECTED_FUNCTION_NAME_TRACKED = tracked_task_def.__name__
    EXPECTED_FILENAME_TRACKED = os.path.basename(tracked_task_def.__code__.co_filename)

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name="background",
            locations=[
                pprof_utils.StackLocation(
                    function_name=EXPECTED_FUNCTION_NAME_BACKGROUND,
                    filename=EXPECTED_FILENAME_BACKGROUND,
                    line_no=EXPECTED_LINE_NO_BACKGROUND,
                ),
            ],
        ),
        print_samples_on_failure=True,
    )

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name=t1_name,
            locations=[
                pprof_utils.StackLocation(
                    function_name=EXPECTED_FUNCTION_NAME_TRACKED,
                    filename=EXPECTED_FILENAME_TRACKED,
                    line_no=-1,  # any line
                )
            ],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.xfail(reason="No way to get the current loop if it is set but not running.")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_start_profiler_from_process_after_creating_loop",
    ),
    err=None,
)
# For macOS: err=None ignores expected stderr from tracer failing to connect to agent (not relevant to this test)
def test_asyncio_start_profiler_from_process_after_creating_loop() -> None:
    import asyncio
    import os
    import sys
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    # Start an asyncio loop BEFORE importing profiler modules
    # This simulates the bug scenario where a loop exists before profiling is enabled
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    assert stack.is_available, stack.failure_msg

    p = profiler.Profiler()
    p.start()

    async def my_function():
        async def background_task_func() -> None:
            """Background task that runs in the existing loop."""
            await asyncio.sleep(1.5)

        # Create and start a task in the existing loop
        background_task = loop.create_task(background_task_func(), name="background")
        assert background_task is not None

        # Run tasks that should be tracked
        sleep_time = 0.2
        loop_run_time = 0.75

        async def tracked_task() -> None:
            start_time = time.time()
            while time.time() < start_time + loop_run_time:
                await asyncio.sleep(sleep_time)

        async def main_task():
            t1 = asyncio.create_task(tracked_task(), name="tracked 1")
            t2 = asyncio.create_task(tracked_task(), name="tracked 2")
            await tracked_task()
            await asyncio.sleep(0.25)
            return t1, t2

        result = await main_task()

        await background_task

        return tracked_task, background_task_func, result

    main_task = loop.create_task(my_function(), name="main")
    tracked_task_def, background_task_def, (t1, t2) = loop.run_until_complete(main_task)

    p.stop()

    t1_name = t1.get_name()
    t2_name = t2.get_name()

    assert t1_name == "tracked 1"
    assert t2_name == "tracked 2"

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)

    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(samples) > 0, "No task names found - existing loop was not tracked!"

    EXPECTED_FILENAME_BACKGROUND = os.path.basename(background_task_def.__code__.co_filename)
    EXPECTED_LINE_NO_BACKGROUND = -1  # any line
    if sys.version_info >= (3, 11):
        EXPECTED_FUNCTION_NAME_BACKGROUND = f"{my_function.__name__}.<locals>.{background_task_def.__name__}"
    else:
        EXPECTED_FUNCTION_NAME_BACKGROUND = background_task_def.__name__

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name="background",
            locations=[
                pprof_utils.StackLocation(
                    function_name=EXPECTED_FUNCTION_NAME_BACKGROUND,
                    filename=EXPECTED_FILENAME_BACKGROUND,
                    line_no=EXPECTED_LINE_NO_BACKGROUND,
                ),
            ],
        ),
        print_samples_on_failure=True,
    )

    # Verify specific tasks are in the profile
    EXPECTED_FILENAME_TRACKED = os.path.basename(tracked_task_def.__code__.co_filename)

    if sys.version_info >= (3, 11):
        EXPECTED_FUNCTION_NAME_TRACKED = f"{my_function.__name__}.<locals>.{tracked_task_def.__name__}"
    else:
        EXPECTED_FUNCTION_NAME_TRACKED = tracked_task_def.__name__

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name=t1_name,
            locations=[
                pprof_utils.StackLocation(
                    function_name=EXPECTED_FUNCTION_NAME_TRACKED,
                    filename=EXPECTED_FILENAME_TRACKED,
                    line_no=-1,  # any line
                )
            ],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.xfail(reason="This test fails because there's no way to get the current loop if it's not already running.")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_import_profiler_from_process_after_starting_loop",
    ),
    err=None,
)
# For macOS: err=None ignores expected stderr from tracer failing to connect to agent (not relevant to this test)
def test_asyncio_import_profiler_from_process_after_starting_loop() -> None:
    import asyncio
    import os
    import sys
    import time

    from tests.profiling.collector import pprof_utils

    # Start an asyncio loop BEFORE importing profiler modules
    # This simulates the bug scenario where a loop exists before profiling is enabled
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    assert stack.is_available, stack.failure_msg

    p = profiler.Profiler()
    p.start()

    async def my_function():
        async def background_task_func() -> None:
            """Background task that runs in the existing loop."""
            await asyncio.sleep(1.5)

        # Create and start a task in the existing loop
        background_task = loop.create_task(background_task_func(), name="background")
        assert background_task is not None

        # Run tasks that should be tracked
        sleep_time = 0.2
        loop_run_time = 0.75

        async def tracked_task() -> None:
            start_time = time.time()
            while time.time() < start_time + loop_run_time:
                await asyncio.sleep(sleep_time)

        async def main_task():
            t1 = asyncio.create_task(tracked_task(), name="tracked 1")
            t2 = asyncio.create_task(tracked_task(), name="tracked 2")
            await tracked_task()
            await asyncio.sleep(0.25)
            return t1, t2

        result = await main_task()

        await background_task

        return tracked_task, background_task_func, result

    main_task = loop.create_task(my_function(), name="main")
    tracked_task_def, background_task_def, (t1, t2) = loop.run_until_complete(main_task)

    p.stop()

    t1_name = t1.get_name()
    t2_name = t2.get_name()

    assert t1_name == "tracked 1"
    assert t2_name == "tracked 2"

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)

    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(samples) > 0, "No task names found - existing loop was not tracked!"

    EXPECTED_FILENAME_BACKGROUND = os.path.basename(background_task_def.__code__.co_filename)
    EXPECTED_LINE_NO_BACKGROUND = -1  # any line
    if sys.version_info >= (3, 11):
        EXPECTED_FUNCTION_NAME_BACKGROUND = f"{my_function.__name__}.<locals>.{background_task_def.__name__}"
    else:
        EXPECTED_FUNCTION_NAME_BACKGROUND = background_task_def.__name__

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name="background",
            locations=[
                pprof_utils.StackLocation(
                    function_name=EXPECTED_FUNCTION_NAME_BACKGROUND,
                    filename=EXPECTED_FILENAME_BACKGROUND,
                    line_no=EXPECTED_LINE_NO_BACKGROUND,
                ),
            ],
        ),
        print_samples_on_failure=True,
    )

    # Verify specific tasks are in the profile
    EXPECTED_FILENAME_TRACKED = os.path.basename(tracked_task_def.__code__.co_filename)

    if sys.version_info >= (3, 11):
        EXPECTED_FUNCTION_NAME_TRACKED = f"{my_function.__name__}.<locals>.{tracked_task_def.__name__}"
    else:
        EXPECTED_FUNCTION_NAME_TRACKED = tracked_task_def.__name__

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name=t1_name,
            locations=[
                pprof_utils.StackLocation(
                    function_name=EXPECTED_FUNCTION_NAME_TRACKED,
                    filename=EXPECTED_FILENAME_TRACKED,
                    line_no=-1,  # any line
                )
            ],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_start_profiler_from_process_after_creating_loop_and_task",
    ),
    err=None,
)
def test_asyncio_start_profiler_from_process_after_task_start() -> None:
    # NOW import profiling modules - this should track the existing loop
    import asyncio
    import os
    import sys
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    # Start an asyncio loop BEFORE importing profiler modules
    # This simulates the bug scenario where a loop exists before profiling is enabled
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def my_function():
        async def background_task_func() -> None:
            """Background task that runs in the existing loop."""
            await asyncio.sleep(2.5)

        # Create and start a task in the existing loop
        background_task = loop.create_task(background_task_func(), name="background")
        assert background_task is not None

        # Start profiler after loop is already running
        assert asyncio.get_running_loop() is loop

        assert stack.is_available, stack.failure_msg

        p = profiler.Profiler()
        p.start()

        # Run tasks that should be tracked
        sleep_time = 0.2
        loop_run_time = 0.75

        async def tracked_task() -> None:
            start_time = time.time()
            while time.time() < start_time + loop_run_time:
                await asyncio.sleep(sleep_time)

        async def main_task():
            t1 = asyncio.create_task(tracked_task(), name="tracked 1")
            t2 = asyncio.create_task(tracked_task(), name="tracked 2")
            await tracked_task()
            await asyncio.sleep(0.25)
            return t1, t2

        result = await main_task()

        await background_task

        return tracked_task, background_task_func, p, result

    main_task = loop.create_task(my_function(), name="main")
    tracked_task_def, background_task_def, p, (t1, t2) = loop.run_until_complete(main_task)

    p.stop()

    t1_name = t1.get_name()
    t2_name = t2.get_name()

    assert t1_name == "tracked 1"
    assert t2_name == "tracked 2"

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)

    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(samples) > 0, "No task names found - existing loop was not tracked!"

    EXPECTED_FILENAME_MAIN = os.path.basename(my_function.__code__.co_filename)
    EXPECTED_LINE_NO_MAIN = -1  # any line
    EXPECTED_FUNCTION_NAME_MAIN = my_function.__name__

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name="main",
            locations=[
                pprof_utils.StackLocation(
                    function_name=EXPECTED_FUNCTION_NAME_MAIN,
                    filename=EXPECTED_FILENAME_MAIN,
                    line_no=EXPECTED_LINE_NO_MAIN,
                ),
            ],
        ),
        print_samples_on_failure=True,
    )

    EXPECTED_FILENAME_BACKGROUND = os.path.basename(background_task_def.__code__.co_filename)
    EXPECTED_LINE_NO_BACKGROUND = -1  # any line
    if sys.version_info >= (3, 11):
        EXPECTED_FUNCTION_NAME_BACKGROUND = f"{my_function.__name__}.<locals>.{background_task_def.__name__}"
    else:
        EXPECTED_FUNCTION_NAME_BACKGROUND = background_task_def.__name__

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name="background",
            locations=[
                pprof_utils.StackLocation(
                    function_name=EXPECTED_FUNCTION_NAME_BACKGROUND,
                    filename=EXPECTED_FILENAME_BACKGROUND,
                    line_no=EXPECTED_LINE_NO_BACKGROUND,
                ),
            ],
        ),
        print_samples_on_failure=True,
    )

    # Verify specific tasks are in the profile
    EXPECTED_FILENAME_TRACKED = os.path.basename(tracked_task_def.__code__.co_filename)

    if sys.version_info >= (3, 11):
        EXPECTED_FUNCTION_NAME_TRACKED = f"{my_function.__name__}.<locals>.{tracked_task_def.__name__}"
    else:
        EXPECTED_FUNCTION_NAME_TRACKED = tracked_task_def.__name__

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            locations=[
                pprof_utils.StackLocation(
                    function_name=EXPECTED_FUNCTION_NAME_TRACKED,
                    filename=EXPECTED_FILENAME_TRACKED,
                    line_no=-1,  # any line
                )
            ],
        ),
    )


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_start_profiler_from_process_after_task_start",
    ),
    err=None,
)
def test_asyncio_import_and_start_profiler_from_process_after_task_start() -> None:
    import asyncio
    import os
    import sys
    import time

    from tests.profiling.collector import pprof_utils

    # Start an asyncio loop BEFORE importing profiler modules
    # This simulates the bug scenario where a loop exists before profiling is enabled
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def my_function():
        async def background_task_func() -> None:
            """Background task that runs in the existing loop."""
            await asyncio.sleep(1.5)

        # Create and start a task in the existing loop
        background_task = loop.create_task(background_task_func(), name="background")
        assert background_task is not None

        # Start profiler after loop is already running
        assert asyncio.get_running_loop() is loop

        # NOW import profiling modules - this should track the existing loop
        from ddtrace.internal.datadog.profiling import stack
        from ddtrace.profiling import profiler

        assert stack.is_available, stack.failure_msg

        p = profiler.Profiler()
        p.start()

        # Run tasks that should be tracked
        sleep_time = 0.2
        loop_run_time = 0.75

        async def tracked_task() -> None:
            start_time = time.time()
            while time.time() < start_time + loop_run_time:
                await asyncio.sleep(sleep_time)

        async def main_task():
            t1 = asyncio.create_task(tracked_task(), name="tracked 1")
            t2 = asyncio.create_task(tracked_task(), name="tracked 2")
            await tracked_task()
            await asyncio.sleep(0.25)
            return t1, t2

        result = await main_task()

        await background_task

        return tracked_task, background_task_func, p, result

    main_task = loop.create_task(my_function(), name="main")
    tracked_task_def, background_task_def, p, (t1, t2) = loop.run_until_complete(main_task)

    p.stop()

    t1_name = t1.get_name()
    t2_name = t2.get_name()

    assert t1_name == "tracked 1"
    assert t2_name == "tracked 2"

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)

    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(samples) > 0, "No task names found - existing loop was not tracked!"

    if sys.version_info >= (3, 11):
        EXPECTED_FUNCTION_NAME_BACKGROUND = f"{my_function.__name__}.<locals>.{background_task_def.__name__}"
    else:
        EXPECTED_FUNCTION_NAME_BACKGROUND = background_task_def.__name__
    EXPECTED_FILENAME_BACKGROUND = os.path.basename(background_task_def.__code__.co_filename)
    EXPECTED_LINE_NO_BACKGROUND = -1  # any line

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name="background",
            locations=[
                pprof_utils.StackLocation(
                    function_name=EXPECTED_FUNCTION_NAME_BACKGROUND,
                    filename=EXPECTED_FILENAME_BACKGROUND,
                    line_no=EXPECTED_LINE_NO_BACKGROUND,
                ),
            ],
        ),
        print_samples_on_failure=True,
    )

    # Verify specific tasks are in the profile
    if sys.version_info >= (3, 11):
        EXPECTED_FUNCTION_NAME_TRACKED = f"{my_function.__name__}.<locals>.{tracked_task_def.__name__}"
    else:
        EXPECTED_FUNCTION_NAME_TRACKED = tracked_task_def.__name__
    EXPECTED_FILENAME_TRACKED = os.path.basename(tracked_task_def.__code__.co_filename)

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            locations=[
                pprof_utils.StackLocation(
                    function_name=EXPECTED_FUNCTION_NAME_TRACKED,
                    filename=EXPECTED_FILENAME_TRACKED,
                    line_no=-1,  # any line
                )
            ],
        ),
        print_samples_on_failure=True,
    )
