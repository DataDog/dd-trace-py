// Unit tests for Python 3.15 frame-state and await-stack changes.
//
// Covered:
//   1. PyGen_yf returns nullptr for FRAME_SUSPENDED_YIELD_FROM_LOCKED in GIL builds (3.15+).
//   2. PyGen_yf returns nullptr for all non-suspended states (3.15+).
//   3. PyGen_yf reads the awaited object from stackpointer[-2] on a frame suspended in
//      YIELD_FROM, and rejects a stack too short to hold that slot.
//
// Memory stub: copy_type/copy_generic call echion_fuzz_copy_memory, which we route to the
// buffer-backed fake process image shared with the fuzz harnesses (fuzz_memory_image.h).
// Tests that only exercise the frame-state guard leave the image detached, so every read
// fails and PyGen_yf bails out at the first copy_type; the await-stack tests attach an
// image so the reads succeed and the returned pointer is meaningful.

#include <echion/cpython/tasks.h>
#include <echion/echion_sampler.h>
#include <echion/vm.h>

#include "fuzz_memory_image.h"

#include <atomic>
#include <cstddef>
#include <cstring>
#include <utility>
#include <vector>

#include <gmock/gmock.h>
#include <gtest/gtest.h>

// Counter tracking how many times copy_memory was invoked. Reset before each
// PyGen_yf call so tests can assert whether the state guard allowed execution
// to reach the copy site (>0) or filtered it out first (0).
// Definition of the ECHION_FUZZING stub already declared in vm.h; atomic so
// future parallel-test runs stay race-free.
static std::atomic<int> g_copy_attempts{ 0 };

extern "C" int
echion_fuzz_copy_memory(proc_ref_t /*proc_ref*/, const void* addr, ssize_t len, void* buf)
{
    g_copy_attempts.fetch_add(1, std::memory_order_relaxed);
    return echion_fuzz_memory_image_read(addr, len, buf);
}

#if PY_VERSION_HEX >= 0x030f0000

// ─────────────────────────────────────────────────────────────────────────────
// PyGen_yf state-check tests (3.15+)
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
    // immediately from the state guard without attempting any memory read.
    g_copy_attempts.store(0, std::memory_order_relaxed);
    auto gen = make_fake_gen(3 /* FRAME_SUSPENDED_YIELD_FROM_LOCKED value */);
    PyObject* result = PyGen_yf(&gen, nullptr);
    EXPECT_EQ(result, nullptr);
    EXPECT_EQ(g_copy_attempts.load(std::memory_order_relaxed), 0)
      << "state guard must filter FRAME_SUSPENDED_YIELD_FROM_LOCKED without any copy attempt";
}

TEST(PyGenYf315GilBuild, SuspendedYieldFromEntersBody)
{
    // FRAME_SUSPENDED_YIELD_FROM must still be recognised as a suspended state.
    // The state guard passes, execution enters the body, and copy_type(nullptr, frame)
    // immediately fails — confirming the guard did NOT filter out this state.
    g_copy_attempts.store(0, std::memory_order_relaxed);
    auto gen = make_fake_gen(FRAME_SUSPENDED_YIELD_FROM);
    PyObject* result = PyGen_yf(&gen, nullptr);
    EXPECT_EQ(result, nullptr); // copy_type fails on nullptr frame_addr
    EXPECT_GT(g_copy_attempts.load(std::memory_order_relaxed), 0)
      << "FRAME_SUSPENDED_YIELD_FROM must pass the state guard and attempt a copy";
}

#endif // !Py_GIL_DISABLED

// Non-suspended states must all return nullptr immediately.
class PyGenYf315OtherStates : public ::testing::TestWithParam<int>
{};

TEST_P(PyGenYf315OtherStates, ReturnsNull)
{
    g_copy_attempts.store(0, std::memory_order_relaxed);
    auto gen = make_fake_gen(GetParam());
    EXPECT_EQ(PyGen_yf(&gen, nullptr), nullptr);
    EXPECT_EQ(g_copy_attempts.load(std::memory_order_relaxed), 0)
      << "non-suspended states must be filtered by the state guard without any copy attempt";
}

INSTANTIATE_TEST_SUITE_P(NonSuspendedStates,
                         PyGenYf315OtherStates,
                         ::testing::Values(FRAME_CREATED,   // 0
                                           FRAME_EXECUTING, // 4
                                           FRAME_CLEARED    // 5
                                           ));

// ─────────────────────────────────────────────────────────────────────────────
// Await-stack layout tests (3.15+)
//
// 3.15.0a8 gave _SEND_GEN_FRAME a `null` operand, so a frame suspended in
// YIELD_FROM holds PyStackRef_NULL at stackpointer[-1] and the awaited object at
// stackpointer[-2] (CPython reads it as _PyFrame_StackPeek(&gen->gi_iframe, 2)).
// These tests build that layout in the fake process image and let the reads
// succeed, so they fail if PyGen_yf goes back to the pre-3.15 [-1] offset.
// ─────────────────────────────────────────────────────────────────────────────

static_assert(sizeof(_PyStackRef) == sizeof(uintptr_t), "the fake image writes stack slots as raw pointer-sized words");

// Image layout, all offsets relative to kRemoteBase. The frame region is padded
// past sizeof(_PyInterpreterFrame) so the data stack that follows localsplus is
// in bounds.
static constexpr size_t kFrameOff = 0;
static constexpr size_t kCodeOff = sizeof(_PyInterpreterFrame) + 8 * sizeof(_PyStackRef);
static constexpr size_t kImageSize = kCodeOff + sizeof(PyCodeObject) + 8 * sizeof(_PyStackRef);

// The awaited object itself is never dereferenced, so it can sit outside the
// image. Its low bits must be clear: PyGen_yf returns BITS_TO_PTR_MASKED(slot),
// which strips the stackref tag bit(s).
static constexpr uintptr_t kAwaitedAddr = kRemoteBase + 0x01000000ULL;

static void
poke_word(std::vector<uint8_t>& image, size_t off, uintptr_t value)
{
    ASSERT_LE(off + sizeof(value), image.size());
    std::memcpy(image.data() + off, &value, sizeof(value));
}

// Builds a fake process image holding a generator frame suspended in YIELD_FROM
// with `stack_entries` values on its data stack, laid out the way CPython 3.15
// leaves it.
static std::vector<uint8_t>
make_await_image(int stack_entries)
{
    std::vector<uint8_t> image(kImageSize, 0);

    // co_nlocalsplus == 0 puts the data stack base right at localsplus.
    const int nlocalsplus = 0;
    std::memcpy(image.data() + kCodeOff + offsetof(PyCodeObject, co_nlocalsplus), &nlocalsplus, sizeof(nlocalsplus));

    const size_t stackbase_off = kFrameOff + offsetof(_PyInterpreterFrame, localsplus);
    poke_word(image, kFrameOff + offsetof(_PyInterpreterFrame, f_executable), kRemoteBase + kCodeOff);
    poke_word(image,
              kFrameOff + offsetof(_PyInterpreterFrame, stackpointer),
              kRemoteBase + stackbase_off + static_cast<size_t>(stack_entries) * sizeof(_PyStackRef));

    // Top of stack: PyStackRef_NULL, copied verbatim so we depend on CPython's
    // own encoding rather than reproducing it.
    _PyStackRef null_ref = PyStackRef_NULL;
    std::memcpy(image.data() + stackbase_off + static_cast<size_t>(stack_entries - 1) * sizeof(_PyStackRef),
                &null_ref,
                sizeof(null_ref));

    if (stack_entries >= 2) {
        poke_word(image, stackbase_off + static_cast<size_t>(stack_entries - 2) * sizeof(_PyStackRef), kAwaitedAddr);
    }

    return image;
}

// Attaches an image to the fake process for the duration of a test.
class AttachedImage
{
  public:
    explicit AttachedImage(std::vector<uint8_t> image)
      : image_(std::move(image))
    {
        set_memory_image(image_.data(), image_.size());
    }

    ~AttachedImage() { set_memory_image(nullptr, 0); }

    AttachedImage(const AttachedImage&) = delete;
    AttachedImage& operator=(const AttachedImage&) = delete;

  private:
    std::vector<uint8_t> image_;
};

static PyObject*
fake_frame_addr()
{
    return reinterpret_cast<PyObject*>(kRemoteBase + kFrameOff);
}

TEST(PyGenYf315AwaitStack, TopSlotMasksToNull)
{
    // Pins the property the fix rests on: the pre-3.15 stackpointer[-1] read
    // lands on PyStackRef_NULL, which masks to nullptr, so the await chain would
    // truncate at the outermost coroutine. This mirrors BITS_TO_PTR_MASKED
    // without expanding it here, since its C-style cast trips -Wold-style-cast
    // outside the SYSTEM-included echion/CPython headers.
    _PyStackRef null_ref = PyStackRef_NULL;
    uintptr_t bits = 0;
    std::memcpy(&bits, &null_ref, sizeof(bits));
    EXPECT_EQ(bits & ~static_cast<uintptr_t>(Py_TAG_REFCNT), uintptr_t{ 0 });
}

TEST(PyGenYf315AwaitStack, ReturnsAwaitedObjectFromSecondFromTopSlot)
{
    AttachedImage image(make_await_image(2));
    g_copy_attempts.store(0, std::memory_order_relaxed);

    auto gen = make_fake_gen(FRAME_SUSPENDED_YIELD_FROM);
    PyObject* result = PyGen_yf(&gen, fake_frame_addr());

    EXPECT_EQ(reinterpret_cast<uintptr_t>(result), kAwaitedAddr)
      << "the awaited object lives at stackpointer[-2] on 3.15; [-1] is PyStackRef_NULL";
    EXPECT_GT(g_copy_attempts.load(std::memory_order_relaxed), 0);
}

TEST(PyGenYf315AwaitStack, ReadsSecondFromTopRegardlessOfStackDepth)
{
    // Extra values below the await pair must not shift which slot is read.
    AttachedImage image(make_await_image(5));

    auto gen = make_fake_gen(FRAME_SUSPENDED_YIELD_FROM);
    EXPECT_EQ(reinterpret_cast<uintptr_t>(PyGen_yf(&gen, fake_frame_addr())), kAwaitedAddr);
}

TEST(PyGenYf315AwaitStack, RejectsStackTooShortForAwaitedSlot)
{
    // A single-entry stack cannot hold stackpointer[-2]; the guard must reject it
    // rather than read below the stack base.
    AttachedImage image(make_await_image(1));

    auto gen = make_fake_gen(FRAME_SUSPENDED_YIELD_FROM);
    EXPECT_EQ(PyGen_yf(&gen, fake_frame_addr()), nullptr);
}

#endif // PY_VERSION_HEX >= 0x030f0000
