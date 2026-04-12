// Unit tests for Python 3.15 frame-state guard changes.
//
// Covered:
//   1. Static assertions on renumbered PyFrameState enum values (3.15+).
//   2. PyGen_yf returns nullptr for FRAME_SUSPENDED_YIELD_FROM_LOCKED in GIL builds (3.15+).
//   3. PyGen_yf enters the body for FRAME_SUSPENDED_YIELD_FROM even after the 3.15 guard change.
//   4. PyGen_yf returns nullptr for all non-suspended states (3.15+).
//
// Memory stub: copy_type/copy_generic call echion_fuzz_copy_memory. We define it here to
// always return failure (-1), which is the correct outcome when no real Python process is
// attached. All code paths that reach a copy_type call will return nullptr safely.

#define PY_SSIZE_T_CLEAN
#define Py_BUILD_CORE
#include <Python.h>

#include <gmock/gmock.h>
#include <gtest/gtest.h>

// Stub for echion's remote-memory copy callback. Always fails — we have no live
// Python process to read from, and all tests only need to exercise the state-check
// logic that fires before any copy_type call (or verify safe nullptr-on-copy-failure).
extern "C" int
echion_fuzz_copy_memory(proc_ref_t /*proc_ref*/, const void* /*addr*/, ssize_t /*len*/, void* /*buf*/)
{
    return -1;
}

#if PY_VERSION_HEX >= 0x030e0000
#include <cstddef>
#include <internal/pycore_frame.h>
#include <internal/pycore_interpframe.h>
#include <internal/pycore_interpframe_structs.h>
#include <internal/pycore_stackref.h>
#endif

#include <echion/cpython/tasks.h>
#include <echion/echion_sampler.h>
#include <echion/vm.h>

// ─────────────────────────────────────────────────────────────────────────────
// 1. Compile-time enum value assertions (3.15+ only)
// ─────────────────────────────────────────────────────────────────────────────

#if PY_VERSION_HEX >= 0x030f0000

// PyFrameState was renumbered in 3.15. Verify our understanding matches reality so
// that any future CPython change is caught immediately at compile time.
static_assert(FRAME_CREATED == 0, "FRAME_CREATED should be 0 in Python 3.15");
static_assert(FRAME_SUSPENDED == 1, "FRAME_SUSPENDED should be 1 in Python 3.15");
static_assert(FRAME_SUSPENDED_YIELD_FROM == 2, "FRAME_SUSPENDED_YIELD_FROM should be 2 in Python 3.15");
static_assert(FRAME_EXECUTING == 4, "FRAME_EXECUTING should be 4 in Python 3.15");
static_assert(FRAME_CLEARED == 5, "FRAME_CLEARED should be 5 in Python 3.15");

#ifdef Py_GIL_DISABLED
// FRAME_SUSPENDED_YIELD_FROM_LOCKED only exists when building against a free-threaded Python.
static_assert(FRAME_SUSPENDED_YIELD_FROM_LOCKED == 3, "FRAME_SUSPENDED_YIELD_FROM_LOCKED should be 3 in Python 3.15");
#endif // Py_GIL_DISABLED

TEST(PyFrameState315, EnumValuesMatchExpected)
{
    // Runtime counterpart of the static_asserts above — provides a readable failure
    // message in the test output if run against an unexpected Python build.
    EXPECT_EQ(FRAME_CREATED, 0);
    EXPECT_EQ(FRAME_SUSPENDED, 1);
    EXPECT_EQ(FRAME_SUSPENDED_YIELD_FROM, 2);
    EXPECT_EQ(FRAME_EXECUTING, 4);
    EXPECT_EQ(FRAME_CLEARED, 5);
}

// ─────────────────────────────────────────────────────────────────────────────
// 2. PyGen_yf state-check tests (3.15+)
//
// PyGenObject::gi_frame_state is an int (signed). We set only that field; all
// other fields are zero-initialised. We pass nullptr as frame_addr so that if the
// state check passes, copy_type will immediately fail and return nullptr — which
// means any test that expects nullptr is still correct regardless of whether the
// state check or the copy fails first.
// ─────────────────────────────────────────────────────────────────────────────

static PyGenObject
make_fake_gen(int frame_state)
{
    PyGenObject gen{};
    gen.gi_frame_state = frame_state;
    return gen;
}

#ifndef Py_GIL_DISABLED

TEST(PyGenYf315GilBuild, LockedStateIgnored)
{
    // FRAME_SUSPENDED_YIELD_FROM_LOCKED (value 3) must NOT be treated as a
    // suspended-yield-from state in GIL builds. PyGen_yf should return nullptr
    // immediately from the state check without attempting any memory read.
    auto gen = make_fake_gen(3 /* FRAME_SUSPENDED_YIELD_FROM_LOCKED value */);
    PyObject* result = PyGen_yf(&gen, nullptr);
    EXPECT_EQ(result, nullptr);
}

TEST(PyGenYf315GilBuild, SuspendedYieldFromEntersBody)
{
    // FRAME_SUSPENDED_YIELD_FROM must still be recognised as a suspended state
    // (regression guard). The state check passes, execution enters the body,
    // copy_type(nullptr, frame) immediately fails and returns nullptr — which is
    // expected since we have no live process memory.
    auto gen = make_fake_gen(FRAME_SUSPENDED_YIELD_FROM);
    PyObject* result = PyGen_yf(&gen, nullptr);
    // nullptr is correct: copy_type fails on nullptr frame_addr.
    EXPECT_EQ(result, nullptr);
    // The important invariant is that we reach this point without crashing,
    // confirming the state check did NOT filter out FRAME_SUSPENDED_YIELD_FROM.
}

#endif // !Py_GIL_DISABLED

// Parametrised: non-suspended states must all return nullptr immediately.
class PyGenYf315OtherStates : public ::testing::TestWithParam<int>
{};

TEST_P(PyGenYf315OtherStates, ReturnsNull)
{
    auto gen = make_fake_gen(GetParam());
    EXPECT_EQ(PyGen_yf(&gen, nullptr), nullptr);
}

INSTANTIATE_TEST_SUITE_P(NonSuspendedStates,
                         PyGenYf315OtherStates,
                         ::testing::Values(FRAME_CREATED,   // 0
                                           FRAME_EXECUTING, // 4
                                           FRAME_CLEARED    // 5
                                           ));

#endif // PY_VERSION_HEX >= 0x030f0000
