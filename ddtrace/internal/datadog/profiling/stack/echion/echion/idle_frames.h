// Detection of Python frames that are known to always be off-CPU.
//
// The stack sampler attributes the CPU time consumed by a thread since the
// previous sample to whatever stack happens to be observed at sampling time.
// When the sampling rate is low compared to the sleep/wait pattern of the
// thread, this systematically biases CPU attribution towards idle frames
// (e.g. a thread that sleeps for 990 ms and runs for 10 ms each second will
// almost always be observed inside time.sleep / Event.wait, even though the
// 10 ms of CPU were really spent elsewhere).
//
// To mitigate this, we maintain an allowlist of Python functions that are
// known to block the calling thread. When the leaf Python frame of a sample
// matches one of these patterns, the CPU time delta is *not* attributed to
// the sample; it is accumulated on the ThreadInfo and folded into the next
// sample whose leaf frame is NOT in the allowlist. See ThreadInfo::sample.
//
// The list is intentionally conservative: only frames whose entire body is
// blocked on a kernel wait should be included. False positives (idle frame
// not on the list) merely keep today's behaviour, but false negatives
// (non-idle frame mistakenly tagged as idle) silently move CPU attribution
// to unrelated callers.

#pragma once

#include <echion/frame.h>

class EchionSampler;

// Returns true when `frame` is the leaf frame of a stack that is, with high
// confidence, blocked outside of the CPU (sleep, lock wait, syscall poll, ...).
bool
is_idle_python_frame(EchionSampler& echion, const Frame& frame);
