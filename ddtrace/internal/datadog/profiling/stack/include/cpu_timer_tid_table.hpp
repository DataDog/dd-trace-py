#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <new>

namespace Datadog {
namespace CpuTimer {

// Sparse, signal-safe mapping from Linux TIDs to per-thread capture state.
//
// Linux TIDs are sparse values in [1, pid_max], so a single directly indexed
// table consumes roughly 32 MiB for pointers and 4 MiB for handler activity on
// a typical pid_max of 4,194,304. This table retains direct indexing without
// materializing that entire range. The small top-level directory is allocated
// once, and fixed-size leaves are allocated on the control path before a timer
// is armed for any TID in that leaf.
//
// Published leaves are intentionally never freed. Signal and fault handlers can
// therefore perform two bounded atomic lookups without locks, allocation, or a
// reclamation protocol for the table itself. reset() clears entries after fork
// while retaining the already published leaves.
template<typename T, size_t PageSize = 1024>
class CpuTimerTidTable
{
    static_assert(PageSize > 0, "TID table pages must contain at least one entry");
    static_assert(std::atomic<T*>::is_always_lock_free, "signal handler requires lock-free state pointer atomics");
    static_assert(std::atomic<bool>::is_always_lock_free, "signal handler requires lock-free activity atomics");

    struct Page
    {
        std::atomic<T*> states[PageSize];
        std::atomic<bool> handler_active[PageSize];

        Page() noexcept { reset(); }

        void reset() noexcept
        {
            for (size_t i = 0; i < PageSize; i++) {
                states[i].store(nullptr, std::memory_order_relaxed);
                handler_active[i].store(false, std::memory_order_relaxed);
            }
        }
    };

    static_assert(std::atomic<Page*>::is_always_lock_free,
                  "signal handler requires lock-free directory pointer atomics");

    std::unique_ptr<std::atomic<Page*>[]> directory_;
    size_t directory_size_ = 0;
    size_t max_tid_ = 0;

    [[nodiscard]] static size_t page_index(uint64_t tid) noexcept { return (static_cast<size_t>(tid) - 1) / PageSize; }

    [[nodiscard]] static size_t page_offset(uint64_t tid) noexcept { return (static_cast<size_t>(tid) - 1) % PageSize; }

    [[nodiscard]] Page* page_for(uint64_t tid) const noexcept
    {
        if (!contains(tid)) {
            return nullptr;
        }
        return directory_[page_index(tid)].load(std::memory_order_acquire);
    }

  public:
    struct HandlerToken
    {
        std::atomic<bool>* active = nullptr;
    };

    CpuTimerTidTable() = default;
    ~CpuTimerTidTable()
    {
        for (size_t i = 0; i < directory_size_; i++) {
            delete directory_[i].load(std::memory_order_relaxed);
        }
    }
    CpuTimerTidTable(const CpuTimerTidTable&) = delete;
    CpuTimerTidTable& operator=(const CpuTimerTidTable&) = delete;

    [[nodiscard]] bool initialize(size_t max_tid) noexcept
    {
        if (directory_ != nullptr) {
            return max_tid == max_tid_;
        }
        if (max_tid == 0) {
            return false;
        }

        const size_t directory_size = (max_tid - 1) / PageSize + 1;
        auto* directory = new (std::nothrow) std::atomic<Page*>[directory_size];
        if (directory == nullptr) {
            return false;
        }
        for (size_t i = 0; i < directory_size; i++) {
            directory[i].store(nullptr, std::memory_order_relaxed);
        }

        directory_.reset(directory);
        directory_size_ = directory_size;
        max_tid_ = max_tid;
        return true;
    }

    [[nodiscard]] bool contains(uint64_t tid) const noexcept { return tid > 0 && tid <= max_tid_; }

    // Called on the normal control path before publishing or arming a timer.
    // Concurrent callers are supported even though the engine currently also
    // serializes registration with its registry mutex.
    [[nodiscard]] bool ensure(uint64_t tid) noexcept
    {
        if (!contains(tid)) {
            return false;
        }

        const size_t index = page_index(tid);
        Page* page = directory_[index].load(std::memory_order_acquire);
        if (page != nullptr) {
            return true;
        }

        auto* candidate = new (std::nothrow) Page();
        if (candidate == nullptr) {
            return false;
        }

        Page* expected = nullptr;
        if (!directory_[index].compare_exchange_strong(
              expected, candidate, std::memory_order_release, std::memory_order_acquire)) {
            delete candidate;
        }
        return true;
    }

    [[nodiscard]] bool publish(uint64_t tid, T* state) noexcept
    {
        Page* page = page_for(tid);
        if (page == nullptr) {
            return false;
        }
        page->states[page_offset(tid)].store(state, std::memory_order_seq_cst);
        return true;
    }

    void clear(uint64_t tid) noexcept
    {
        Page* page = page_for(tid);
        if (page != nullptr) {
            page->states[page_offset(tid)].store(nullptr, std::memory_order_seq_cst);
        }
    }

    [[nodiscard]] T* load(uint64_t tid) const noexcept
    {
        Page* page = page_for(tid);
        if (page == nullptr) {
            return nullptr;
        }
        return page->states[page_offset(tid)].load(std::memory_order_seq_cst);
    }

    // Marks this TID active before loading its state. This ordering pairs with
    // clear() followed by is_handler_active() on the reclamation path.
    [[nodiscard]] bool enter_handler(uint64_t tid, T*& state, HandlerToken& token) noexcept
    {
        state = nullptr;
        token.active = nullptr;
        Page* page = page_for(tid);
        if (page == nullptr) {
            return false;
        }
        const size_t offset = page_offset(tid);
        token.active = &page->handler_active[offset];
        token.active->store(true, std::memory_order_seq_cst);
        state = page->states[offset].load(std::memory_order_seq_cst);
        return true;
    }

    static void leave_handler(HandlerToken token) noexcept
    {
        if (token.active != nullptr) {
            token.active->store(false, std::memory_order_seq_cst);
        }
    }

    [[nodiscard]] bool is_handler_active(uint64_t tid) const noexcept
    {
        Page* page = page_for(tid);
        if (page == nullptr) {
            return false;
        }
        return page->handler_active[page_offset(tid)].load(std::memory_order_seq_cst);
    }

    // Only called when no inherited signal handler can still be executing, such
    // as immediately after fork in the child.
    void reset() noexcept
    {
        for (size_t i = 0; i < directory_size_; i++) {
            Page* page = directory_[i].load(std::memory_order_relaxed);
            if (page != nullptr) {
                page->reset();
            }
        }
    }

    [[nodiscard]] size_t allocated_page_count() const noexcept
    {
        size_t count = 0;
        for (size_t i = 0; i < directory_size_; i++) {
            if (directory_[i].load(std::memory_order_acquire) != nullptr) {
                count++;
            }
        }
        return count;
    }

    [[nodiscard]] size_t directory_size() const noexcept { return directory_size_; }
    [[nodiscard]] size_t max_tid() const noexcept { return max_tid_; }
};

} // namespace CpuTimer
} // namespace Datadog
