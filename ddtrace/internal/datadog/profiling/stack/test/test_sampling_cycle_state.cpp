#include "echion/echion_sampler.h"
#include "echion/task_name.h"
#include "sampler.hpp"

#include <gtest/gtest.h>

#include <memory>
#include <optional>

namespace {

constexpr char STACK_CAPTURE_CONTEXT[] = "StackCaptureContext";

struct StackCaptureContext
{
    EchionSampler* echion;
    FrameStack stack;
    std::optional<UnwindResult> unwind_result;
};

PyObject*
capture_stack(PyObject* capsule, PyObject* Py_UNUSED(args))
{
    auto* context = static_cast<StackCaptureContext*>(PyCapsule_GetPointer(capsule, STACK_CAPTURE_CONTEXT));
    if (context == nullptr) {
        return nullptr;
    }

    auto unwind_result = unwind_python_stack(*context->echion, PyThreadState_Get(), context->stack, 2);
    if (!unwind_result) {
        PyErr_SetString(PyExc_RuntimeError, "failed to unwind test stack");
        return nullptr;
    }

    context->unwind_result = *unwind_result;
    Py_RETURN_NONE;
}

} // namespace

#if defined PL_LINUX
TEST(ThreadInfoCreate, IgnoresNonPthreadPythonThreadId)
{
    // Linux limits TIDs to less than 2^22, so this exercises clock_gettime(EINVAL).
    constexpr unsigned long invalid_native_id = 1UL << 24;
    auto thread = ThreadInfo::create(1, invalid_native_id, "test-thread");

    ASSERT_TRUE(thread);
    EXPECT_EQ((*thread)->thread_id, 1);
    EXPECT_EQ((*thread)->cpu_time, 0);
}
#endif

TEST(SamplingCycleState, GCFrameScopeRestoresPreviousFrame)
{
    EchionSampler echion;
    PyObject outer_frame{};
    PyObject inner_frame{};

    EXPECT_EQ(echion.current_gc_frame(), nullptr);
    {
        auto outer_scope = echion.use_gc_frame(&outer_frame);
        EXPECT_EQ(echion.current_gc_frame(), &outer_frame);
        {
            auto inner_scope = echion.use_gc_frame(&inner_frame);
            EXPECT_EQ(echion.current_gc_frame(), &inner_frame);
        }
        EXPECT_EQ(echion.current_gc_frame(), &outer_frame);
    }
    EXPECT_EQ(echion.current_gc_frame(), nullptr);
}

TEST(SamplingCycleState, UnwindReplacesTaskAndGreenletStacksFromPriorCycle)
{
    EchionSampler echion;
#if defined PL_LINUX
    ThreadInfo thread(1, 1, "test-thread", CLOCK_THREAD_CPUTIME_ID);
#elif defined PL_DARWIN
    ThreadInfo thread(1, 1, "test-thread", mach_thread_self());
#endif
    PyThreadState empty_tstate{};

    thread.current_tasks.push_back(std::make_unique<StackInfo>(TaskName::from_literal("stale-task"), false, 1));
    thread.current_greenlets.push_back(std::make_unique<StackInfo>(TaskName::from_literal("stale-greenlet"), false, 2));

    auto result = thread.unwind(echion, &empty_tstate, 0);
#if PY_VERSION_HEX >= 0x030b0000 && PY_VERSION_HEX < 0x030d0000
    // A null C frame must fail, not masquerade as a successfully unwound empty stack.
    EXPECT_FALSE(result);
    EXPECT_EQ(result.error(), ErrorKind::FrameError);
#else
    EXPECT_TRUE(result);
#endif
    EXPECT_EQ(thread.python_stack_unwind_result.frames_added, 0);

    EXPECT_TRUE(thread.current_tasks.empty());
    EXPECT_TRUE(thread.current_greenlets.empty());
}

TEST(StackUnwind, ReportsFramesAndTruncationAtLimit)
{
    if (!Py_IsInitialized()) {
        Py_Initialize();
    }
    _set_pid(getpid());

    EchionSampler echion;
    StackCaptureContext context{ &echion, {}, std::nullopt };
    PyObject* capsule = PyCapsule_New(&context, STACK_CAPTURE_CONTEXT, nullptr);
    ASSERT_NE(capsule, nullptr);

    static PyMethodDef capture_method = {
        "capture_stack",
        capture_stack,
        METH_NOARGS,
        nullptr,
    };
    PyObject* capture = PyCFunction_NewEx(&capture_method, capsule, nullptr);
    ASSERT_NE(capture, nullptr);

    PyObject* globals = PyDict_New();
    ASSERT_NE(globals, nullptr);
    ASSERT_EQ(PyDict_SetItemString(globals, "__builtins__", PyEval_GetBuiltins()), 0);
    ASSERT_EQ(PyDict_SetItemString(globals, "capture_stack", capture), 0);

    PyObject* result = PyRun_String(R"(
def outer():
    middle()
def middle():
    inner()
def inner():
    capture_stack()
outer()
)",
                                    Py_file_input,
                                    globals,
                                    globals);
    if (result == nullptr) {
        PyErr_Print();
    }
    ASSERT_NE(result, nullptr);
    ASSERT_TRUE(context.unwind_result.has_value());
    EXPECT_EQ(context.stack.size(), 2);
    EXPECT_EQ(context.unwind_result->frames_added, 2);
    EXPECT_EQ(context.unwind_result->truncation, TruncationStatus::Truncated);

    Py_DECREF(result);
    Py_DECREF(globals);
    Py_DECREF(capture);
    Py_DECREF(capsule);
}

TEST(StackUnwind, DisabledDetectionRemainsUnknown)
{
    EchionSampler echion;
    FrameStack stack;
    auto result = unwind_frame(echion, nullptr, stack, 1, false);
    EXPECT_EQ(result.frames_added, 0);
    EXPECT_EQ(result.truncation, TruncationStatus::Unknown);

    result = unwind_frame(echion, nullptr, stack, 1, true);
    EXPECT_EQ(result.truncation, TruncationStatus::NotTruncated);
}

#if PY_VERSION_HEX >= 0x030c0000
TEST(StackUnwind, ProbeBudgetExhaustionRemainsUnknown)
{
    _set_pid(getpid());
    EchionSampler echion;
    FrameStack stack;
    std::vector<_PyInterpreterFrame> frames(MAX_TASK_FRAMES + 1);
    for (size_t i = 0; i < frames.size(); i++) {
#if PY_VERSION_HEX >= 0x030e0000
        frames[i].owner = FRAME_OWNED_BY_INTERPRETER;
#else
        frames[i].owner = FRAME_OWNED_BY_CSTACK;
#endif
        frames[i].previous = i + 1 < frames.size() ? &frames[i + 1] : nullptr;
    }

    // Ignored frames do not prove truncation, even when lookahead reaches its safety ceiling.
    std::unordered_set<PyObject*> seen_frames;
    auto result = unwind_frame(echion, reinterpret_cast<PyObject*>(frames.data()), stack, seen_frames, 0, true);
    EXPECT_EQ(seen_frames.size(), MAX_TASK_FRAMES);
    EXPECT_EQ(result.frames_added, 0);
    EXPECT_EQ(result.truncation, TruncationStatus::Unknown);

    // Within the probe budget, the same ignored chain can prove the stack is complete.
    frames[MAX_TASK_FRAMES - 1].previous = nullptr;
    result = unwind_frame(echion, reinterpret_cast<PyObject*>(frames.data()), stack, seen_frames, 0, true);
    EXPECT_EQ(result.truncation, TruncationStatus::NotTruncated);

    result = unwind_frame(echion, reinterpret_cast<PyObject*>(frames.data()), stack, seen_frames, 0, false);
    EXPECT_TRUE(seen_frames.empty());
    EXPECT_EQ(result.truncation, TruncationStatus::Unknown);
}
#endif

TEST(SamplingCycleState, GreenletSwitchPreservesLinkedParentFrame)
{
    constexpr GreenletInfo::ID child_id = 101;
    constexpr GreenletInfo::ID parent_id = 102;
    PyObject child_running_frame{};
    PyObject child_suspended_frame{};
    PyObject parent_suspended_frame{};
    PyObject parent_resumed_frame{};

    Datadog::Sampler& sampler = Datadog::Sampler::get();
    EchionSampler& echion = sampler.get_echion();
    {
        std::lock_guard<std::mutex> guard(echion.greenlet_info_map_lock());
        auto& greenlets = echion.greenlet_info_map();
        greenlets.emplace(
          child_id, std::make_unique<GreenletInfo>(child_id, &child_running_frame, TaskName::from_literal("child")));
        greenlets.emplace(
          parent_id,
          std::make_unique<GreenletInfo>(parent_id, &parent_suspended_frame, TaskName::from_literal("parent")));
    }
    sampler.link_greenlets(parent_id, child_id);

    sampler.record_greenlet_switch(child_id, &child_suspended_frame, parent_id, &parent_resumed_frame, false);
    {
        std::lock_guard<std::mutex> guard(echion.greenlet_info_map_lock());
        EXPECT_EQ(echion.greenlet_parent_map().at(child_id), parent_id);
        EXPECT_EQ(echion.greenlet_info_map().at(child_id)->frame, &child_suspended_frame);
        EXPECT_EQ(echion.greenlet_info_map().at(parent_id)->frame, &parent_suspended_frame);
    }

    sampler.record_greenlet_switch(child_id, &child_running_frame, parent_id, &parent_resumed_frame, true);
    {
        std::lock_guard<std::mutex> guard(echion.greenlet_info_map_lock());
        EXPECT_EQ(echion.greenlet_info_map().at(child_id)->frame, &child_running_frame);
        EXPECT_EQ(echion.greenlet_info_map().at(parent_id)->frame, &parent_resumed_frame);
        echion.greenlet_info_map().erase(child_id);
        echion.greenlet_info_map().erase(parent_id);
        echion.greenlet_parent_map().erase(child_id);
    }
}
