// Compile-time contracts for CPython internal enum values that echion depends on.
//
// Each static_assert fires at *compile time* against the actual CPython headers —
// if CPython renumbers or removes an enum value the build breaks immediately,
// before any test runner is invoked.  The matching gtest TEST() wrappers surface
// the same checks as human-readable failures in CI output.
//
// Update these blocks when adding support for a new CPython minor version:
//   1. Add a new versioned block with the new values.
//   2. Adjust the upper-bound on the previous block if values changed.
//   3. Run the build against the new CPython to confirm all assertions pass.
//
// Enums covered:
//   _frameowner   (pycore_interpframe_structs.h, 3.12+)
//   PyFrameState  (pycore_frame.h, 3.11+)

#define PY_SSIZE_T_CLEAN
#define Py_BUILD_CORE
#include <Python.h>

#include <gtest/gtest.h>

#if PY_VERSION_HEX >= 0x030e0000
#include <internal/pycore_frame.h>
#include <internal/pycore_interpframe.h>
#include <internal/pycore_interpframe_structs.h>
#elif PY_VERSION_HEX >= 0x030b0000
#include <internal/pycore_frame.h>
#endif

// echion/vm.h defines proc_ref_t, which the stub below requires.
#include <echion/vm.h>

// Stub: echion's remote-memory callback is referenced at link time via vm.h.
// Always returns failure — no live process attached in unit tests.
extern "C" int
echion_fuzz_copy_memory(proc_ref_t /*proc_ref*/, const void* /*addr*/, ssize_t /*len*/, void* /*buf*/)
{
    return -1;
}

// ─────────────────────────────────────────────────────────────────────────────
// _frameowner enum  (pycore_interpframe_structs.h, introduced in 3.12)
// ─────────────────────────────────────────────────────────────────────────────

// 3.12 – 3.13: four members, CSTACK=3, no INTERPRETER
#if PY_VERSION_HEX >= 0x030c0000 && PY_VERSION_HEX < 0x030e0000
static_assert(FRAME_OWNED_BY_THREAD == 0,
              "AIDEV-NOTE: _frameowner::FRAME_OWNED_BY_THREAD changed value — update frame.cc owner switch");
static_assert(FRAME_OWNED_BY_GENERATOR == 1,
              "AIDEV-NOTE: _frameowner::FRAME_OWNED_BY_GENERATOR changed value — update frame.cc owner switch");
static_assert(FRAME_OWNED_BY_FRAME_OBJECT == 2,
              "AIDEV-NOTE: _frameowner::FRAME_OWNED_BY_FRAME_OBJECT changed value — update frame.cc owner switch");
static_assert(FRAME_OWNED_BY_CSTACK == 3,
              "AIDEV-NOTE: _frameowner::FRAME_OWNED_BY_CSTACK changed value — update frame.cc owner switch");

TEST(FrameOwnerEnum_312_313, ValuesMatchExpected)
{
    EXPECT_EQ(FRAME_OWNED_BY_THREAD, 0);
    EXPECT_EQ(FRAME_OWNED_BY_GENERATOR, 1);
    EXPECT_EQ(FRAME_OWNED_BY_FRAME_OBJECT, 2);
    EXPECT_EQ(FRAME_OWNED_BY_CSTACK, 3);
}
#endif // 3.12 – 3.13

// 3.14: five members, INTERPRETER added (=3), CSTACK bumped to 4
#if PY_VERSION_HEX >= 0x030e0000 && PY_VERSION_HEX < 0x030f0000
static_assert(FRAME_OWNED_BY_THREAD == 0,
              "AIDEV-NOTE: _frameowner::FRAME_OWNED_BY_THREAD changed value — update frame.cc owner switch");
static_assert(FRAME_OWNED_BY_GENERATOR == 1,
              "AIDEV-NOTE: _frameowner::FRAME_OWNED_BY_GENERATOR changed value — update frame.cc owner switch");
static_assert(FRAME_OWNED_BY_FRAME_OBJECT == 2,
              "AIDEV-NOTE: _frameowner::FRAME_OWNED_BY_FRAME_OBJECT changed value — update frame.cc owner switch");
static_assert(FRAME_OWNED_BY_INTERPRETER == 3,
              "AIDEV-NOTE: _frameowner::FRAME_OWNED_BY_INTERPRETER changed value — update frame.cc owner switch");
static_assert(FRAME_OWNED_BY_CSTACK == 4,
              "AIDEV-NOTE: _frameowner::FRAME_OWNED_BY_CSTACK changed value — update frame.cc owner switch");

TEST(FrameOwnerEnum_314, ValuesMatchExpected)
{
    EXPECT_EQ(FRAME_OWNED_BY_THREAD, 0);
    EXPECT_EQ(FRAME_OWNED_BY_GENERATOR, 1);
    EXPECT_EQ(FRAME_OWNED_BY_FRAME_OBJECT, 2);
    EXPECT_EQ(FRAME_OWNED_BY_INTERPRETER, 3);
    EXPECT_EQ(FRAME_OWNED_BY_CSTACK, 4);
}
#endif // 3.14

// 3.15+: FRAME_OWNED_BY_CSTACK removed
#if PY_VERSION_HEX >= 0x030f0000
static_assert(FRAME_OWNED_BY_THREAD == 0,
              "AIDEV-NOTE: _frameowner::FRAME_OWNED_BY_THREAD changed value — update frame.cc owner switch");
static_assert(FRAME_OWNED_BY_GENERATOR == 1,
              "AIDEV-NOTE: _frameowner::FRAME_OWNED_BY_GENERATOR changed value — update frame.cc owner switch");
static_assert(FRAME_OWNED_BY_FRAME_OBJECT == 2,
              "AIDEV-NOTE: _frameowner::FRAME_OWNED_BY_FRAME_OBJECT changed value — update frame.cc owner switch");
static_assert(FRAME_OWNED_BY_INTERPRETER == 3,
              "AIDEV-NOTE: _frameowner::FRAME_OWNED_BY_INTERPRETER changed value — update frame.cc owner switch");
// FRAME_OWNED_BY_CSTACK intentionally not listed — it was removed in 3.15.
// If this file compiles without error, CPython has not re-introduced it.

TEST(FrameOwnerEnum_315, ValuesMatchExpected)
{
    EXPECT_EQ(FRAME_OWNED_BY_THREAD, 0);
    EXPECT_EQ(FRAME_OWNED_BY_GENERATOR, 1);
    EXPECT_EQ(FRAME_OWNED_BY_FRAME_OBJECT, 2);
    EXPECT_EQ(FRAME_OWNED_BY_INTERPRETER, 3);
}
#endif // 3.15+

// ─────────────────────────────────────────────────────────────────────────────
// PyFrameState / gi_frame_state  (pycore_frame.h, introduced in 3.11)
// ─────────────────────────────────────────────────────────────────────────────

// 3.11 – 3.12: negative-valued range, no FRAME_SUSPENDED_YIELD_FROM yet.
// FRAME_SUSPENDED_YIELD_FROM was introduced in 3.13 (CPython gh-104210), which
// also shifted FRAME_CREATED and FRAME_SUSPENDED one slot more negative.
#if PY_VERSION_HEX >= 0x030b0000 && PY_VERSION_HEX < 0x030d0000
static_assert(FRAME_CREATED == -2,
              "AIDEV-NOTE: PyFrameState::FRAME_CREATED changed value — update tasks.h PyGen_yf and tasks.cc");
static_assert(FRAME_SUSPENDED == -1, "AIDEV-NOTE: PyFrameState::FRAME_SUSPENDED changed value");
static_assert(FRAME_EXECUTING == 0,
              "AIDEV-NOTE: PyFrameState::FRAME_EXECUTING changed value — update tasks.cc gen_is_running check");
// FRAME_COMPLETED == 1 is not used by echion directly; omitted intentionally.
static_assert(FRAME_CLEARED == 4,
              "AIDEV-NOTE: PyFrameState::FRAME_CLEARED changed value — update tasks.cc gi_frame_state check");

TEST(PyFrameStateEnum_311_312, ValuesMatchExpected)
{
    EXPECT_EQ(FRAME_CREATED, -2);
    EXPECT_EQ(FRAME_SUSPENDED, -1);
    EXPECT_EQ(FRAME_EXECUTING, 0);
    EXPECT_EQ(FRAME_CLEARED, 4);
}
#endif // 3.11 – 3.12

// 3.13 – 3.14: negative-valued range, FRAME_SUSPENDED_YIELD_FROM added (-1),
// pushing FRAME_CREATED to -3 and FRAME_SUSPENDED to -2.
#if PY_VERSION_HEX >= 0x030d0000 && PY_VERSION_HEX < 0x030f0000
static_assert(FRAME_CREATED == -3,
              "AIDEV-NOTE: PyFrameState::FRAME_CREATED changed value — update tasks.h PyGen_yf and tasks.cc");
static_assert(FRAME_SUSPENDED == -2, "AIDEV-NOTE: PyFrameState::FRAME_SUSPENDED changed value");
static_assert(FRAME_SUSPENDED_YIELD_FROM == -1,
              "AIDEV-NOTE: PyFrameState::FRAME_SUSPENDED_YIELD_FROM changed value — update tasks.h PyGen_yf");
static_assert(FRAME_EXECUTING == 0,
              "AIDEV-NOTE: PyFrameState::FRAME_EXECUTING changed value — update tasks.cc gen_is_running check");
// FRAME_COMPLETED == 1 is not used by echion directly; omitted intentionally.
static_assert(FRAME_CLEARED == 4,
              "AIDEV-NOTE: PyFrameState::FRAME_CLEARED changed value — update tasks.cc gi_frame_state check");

TEST(PyFrameStateEnum_313_314, ValuesMatchExpected)
{
    EXPECT_EQ(FRAME_CREATED, -3);
    EXPECT_EQ(FRAME_SUSPENDED, -2);
    EXPECT_EQ(FRAME_SUSPENDED_YIELD_FROM, -1);
    EXPECT_EQ(FRAME_EXECUTING, 0);
    EXPECT_EQ(FRAME_CLEARED, 4);
}
#endif // 3.13 – 3.14

// 3.15+: all values renumbered, COMPLETED removed
#if PY_VERSION_HEX >= 0x030f0000
static_assert(FRAME_CREATED == 0,
              "AIDEV-NOTE: PyFrameState::FRAME_CREATED changed value — update tasks.h PyGen_yf and tasks.cc");
static_assert(FRAME_SUSPENDED == 1, "AIDEV-NOTE: PyFrameState::FRAME_SUSPENDED changed value");
static_assert(FRAME_SUSPENDED_YIELD_FROM == 2,
              "AIDEV-NOTE: PyFrameState::FRAME_SUSPENDED_YIELD_FROM changed value — update tasks.h PyGen_yf");
// value 3 is FRAME_SUSPENDED_YIELD_FROM_LOCKED in free-threaded builds (see below)
static_assert(FRAME_EXECUTING == 4,
              "AIDEV-NOTE: PyFrameState::FRAME_EXECUTING changed value — update tasks.cc gen_is_running check");
static_assert(FRAME_CLEARED == 5,
              "AIDEV-NOTE: PyFrameState::FRAME_CLEARED changed value — update tasks.cc gi_frame_state check");
// FRAME_COMPLETED intentionally not listed — it was removed in 3.15.

#ifdef Py_GIL_DISABLED
static_assert(
  FRAME_SUSPENDED_YIELD_FROM_LOCKED == 3,
  "AIDEV-NOTE: FRAME_SUSPENDED_YIELD_FROM_LOCKED changed value — update tasks.h PyGen_yf (Py_GIL_DISABLED)");
#endif

TEST(PyFrameStateEnum_315, ValuesMatchExpected)
{
    EXPECT_EQ(FRAME_CREATED, 0);
    EXPECT_EQ(FRAME_SUSPENDED, 1);
    EXPECT_EQ(FRAME_SUSPENDED_YIELD_FROM, 2);
    EXPECT_EQ(FRAME_EXECUTING, 4);
    EXPECT_EQ(FRAME_CLEARED, 5);
}

#ifdef Py_GIL_DISABLED
TEST(PyFrameStateEnum_315_NoGIL, LockedYieldFromValueMatchesExpected)
{
    EXPECT_EQ(FRAME_SUSPENDED_YIELD_FROM_LOCKED, 3);
}
#endif

#endif // 3.15+
