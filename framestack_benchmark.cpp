// Benchmark comparing FrameStack to std::deque
// Compile with: g++ -std=c++17 -O3 -DNDEBUG framestack_benchmark.cpp -lbenchmark -lpthread -o framestack_benchmark
// Run with: ./framestack_benchmark

#include <algorithm>
#include <benchmark/benchmark.h>
#include <cassert>
#include <deque>
#include <functional>
#include <optional>
#include <vector>

// Simplified Frame structure for benchmarking (without Python dependencies)
struct Frame
{
    using Ref = std::reference_wrapper<Frame>;
    using Key = uintptr_t;

    Key cache_key = 0;
    int filename = 0;
    int name = 0;
    int line = 0;
    int column = 0;

    Frame(int filename = 0, int name = 0)
      : filename(filename)
      , name(name)
    {
    }
};

// FrameStack implementation (copied from stacks.h with minor adjustments)
class FrameStack
{
    static constexpr size_t initial_capacity = 2048;

  private:
    std::vector<std::optional<Frame::Ref>> buffer_;
    size_t start_; // Index of the first element
    size_t end_;   // Index one past the last element

    void grow(bool at_construct = false)
    {
        std::abort();
        size_t current_size = end_ - start_;
        size_t new_capacity = std::max(buffer_.capacity() * 2, FrameStack::initial_capacity);

        std::vector<std::optional<Frame::Ref>> new_buffer;
        new_buffer.reserve(new_capacity);
        new_buffer.resize(new_capacity);

        // Center elements in the new buffer with equal space at front and back
        size_t new_start = (new_capacity - current_size) / 2;
        for (size_t i = 0; i < current_size; ++i) {
            new_buffer[new_start + i] = std::move(buffer_[start_ + i]);
        }

        buffer_ = std::move(new_buffer);
        start_ = new_start;
        end_ = new_start + current_size;
    }

    // Helper iterator adapter to unwrap optionals
    template<typename Iter>
    class unwrap_iterator
    {
      private:
        Iter it_;

      public:
        using iterator_category = typename std::iterator_traits<Iter>::iterator_category;
        using value_type = Frame::Ref;
        using difference_type = typename std::iterator_traits<Iter>::difference_type;
        using pointer = Frame*;
        using reference = Frame::Ref;

        unwrap_iterator(Iter it)
          : it_(it)
        {
        }

        Frame::Ref operator*() const { return **it_; }
        Frame* operator->() const { return &((**it_).get()); }

        unwrap_iterator& operator++()
        {
            ++it_;
            return *this;
        }
        unwrap_iterator operator++(int)
        {
            auto tmp = *this;
            ++(*this);
            return tmp;
        }
        unwrap_iterator& operator--()
        {
            --it_;
            return *this;
        }
        unwrap_iterator operator--(int)
        {
            auto tmp = *this;
            --(*this);
            return tmp;
        }

        unwrap_iterator operator+(difference_type n) const { return unwrap_iterator(it_ + n); }
        unwrap_iterator operator-(difference_type n) const { return unwrap_iterator(it_ - n); }
        difference_type operator-(const unwrap_iterator& other) const { return it_ - other.it_; }

        bool operator==(const unwrap_iterator& other) const { return it_ == other.it_; }
        bool operator!=(const unwrap_iterator& other) const { return it_ != other.it_; }
        bool operator<(const unwrap_iterator& other) const { return it_ < other.it_; }
        bool operator<=(const unwrap_iterator& other) const { return it_ <= other.it_; }
        bool operator>(const unwrap_iterator& other) const { return it_ > other.it_; }
        bool operator>=(const unwrap_iterator& other) const { return it_ >= other.it_; }

        friend unwrap_iterator operator+(difference_type n, const unwrap_iterator& it) { return it + n; }
    };

  public:
    using Key = Frame::Key;
    using value_type = Frame::Ref;
    using reference = Frame::Ref;
    using const_reference = Frame::Ref;
    using iterator = unwrap_iterator<typename std::vector<std::optional<Frame::Ref>>::iterator>;
    using const_iterator = unwrap_iterator<typename std::vector<std::optional<Frame::Ref>>::const_iterator>;
    using reverse_iterator = std::reverse_iterator<iterator>;
    using const_reverse_iterator = std::reverse_iterator<const_iterator>;

    FrameStack()
      : start_(initial_capacity / 2)
      , end_(initial_capacity / 2)
    {
        buffer_.reserve(initial_capacity);
        buffer_.resize(initial_capacity);
    }

    size_t size() const { return end_ - start_; }
    bool empty() const { return start_ == end_; }

    void clear()
    {
        buffer_.clear();
        buffer_.reserve(initial_capacity);
        buffer_.resize(initial_capacity);
        start_ = initial_capacity / 2;
        end_ = initial_capacity / 2;
    }

    void push_back(const Frame::Ref& value)
    {
        if (end_ >= buffer_.capacity()) {
            grow();
        }
        if (end_ >= buffer_.size()) {
            buffer_.resize(end_ + 1);
        }
        buffer_[end_++] = value;
    }

    void push_front(const Frame::Ref& value)
    {
        if (start_ == 0) {
            grow();
        }
        buffer_[--start_] = value;
    }

    void pop_back()
    {
        assert(!empty() && "pop_back on empty FrameStack");
        --end_;
    }

    void pop_front()
    {
        assert(!empty() && "pop_front on empty FrameStack");
        ++start_;
    }

    Frame::Ref operator[](size_t index)
    {
        assert(index < size() && "operator[] index out of bounds");
        return *buffer_[start_ + index];
    }

    Frame::Ref operator[](size_t index) const
    {
        assert(index < size() && "operator[] index out of bounds");
        return *buffer_[start_ + index];
    }

    iterator begin() { return iterator(buffer_.begin() + start_); }
    iterator end() { return iterator(buffer_.begin() + end_); }
    const_iterator begin() const { return const_iterator(buffer_.begin() + start_); }
    const_iterator end() const { return const_iterator(buffer_.begin() + end_); }

    reverse_iterator rbegin() { return reverse_iterator(end()); }
    reverse_iterator rend() { return reverse_iterator(begin()); }
    const_reverse_iterator rbegin() const { return const_reverse_iterator(end()); }
    const_reverse_iterator rend() const { return const_reverse_iterator(begin()); }
};

// ============================================================================
// Benchmarks: Construction / Destruction
// ============================================================================

static void
BM_FrameStack_Construction(benchmark::State& state)
{
    for (auto _ : state) {
        static FrameStack stack;
        stack.clear();
        benchmark::DoNotOptimize(stack);
    }
}
BENCHMARK(BM_FrameStack_Construction);

static void
BM_Deque_Construction(benchmark::State& state)
{
    for (auto _ : state) {
        std::deque<std::reference_wrapper<Frame>> deque;
        benchmark::DoNotOptimize(deque);
    }
}
BENCHMARK(BM_Deque_Construction);

// ============================================================================
// Benchmarks: Push Back
// ============================================================================

static void
BM_FrameStack_PushBack(benchmark::State& state)
{
    Frame frame1(1, 1), frame2(2, 2), frame3(3, 3);
    for (auto _ : state) {
        static FrameStack stack;
        stack.clear();
        for (int64_t i = 0; i < state.range(0); ++i) {
            stack.push_back(std::ref(frame1));
        }
        benchmark::DoNotOptimize(stack);
    }
}
BENCHMARK(BM_FrameStack_PushBack)->Range(8, 2048);

static void
BM_Deque_PushBack(benchmark::State& state)
{
    Frame frame1(1, 1), frame2(2, 2), frame3(3, 3);
    for (auto _ : state) {
        std::deque<std::reference_wrapper<Frame>> deque;
        for (int64_t i = 0; i < state.range(0); ++i) {
            deque.push_back(std::ref(frame1));
        }
        benchmark::DoNotOptimize(deque);
    }
}
BENCHMARK(BM_Deque_PushBack)->Range(8, 2048);

// ============================================================================
// Benchmarks: Push Front
// ============================================================================

static void
BM_FrameStack_PushFront(benchmark::State& state)
{
    Frame frame1(1, 1), frame2(2, 2), frame3(3, 3);
    for (auto _ : state) {
        static FrameStack stack;
        stack.clear();
        for (int64_t i = 0; i < state.range(0); ++i) {
            stack.push_front(std::ref(frame1));
        }
        benchmark::DoNotOptimize(stack);
    }
}
BENCHMARK(BM_FrameStack_PushFront)->Range(8, 2048);

static void
BM_Deque_PushFront(benchmark::State& state)
{
    Frame frame1(1, 1), frame2(2, 2), frame3(3, 3);
    for (auto _ : state) {
        std::deque<std::reference_wrapper<Frame>> deque;
        for (int64_t i = 0; i < state.range(0); ++i) {
            deque.push_front(std::ref(frame1));
        }
        benchmark::DoNotOptimize(deque);
    }
}
BENCHMARK(BM_Deque_PushFront)->Range(8, 2048);

// ============================================================================
// Benchmarks: Mixed Push (alternating back and front)
// ============================================================================

static void
BM_FrameStack_MixedPush(benchmark::State& state)
{
    Frame frame1(1, 1), frame2(2, 2);
    for (auto _ : state) {
        static FrameStack stack;
        stack.clear();
        for (int64_t i = 0; i < state.range(0); ++i) {
            if (i % 2 == 0) {
                stack.push_back(std::ref(frame1));
            } else {
                stack.push_front(std::ref(frame2));
            }
        }
        benchmark::DoNotOptimize(stack);
    }
}
BENCHMARK(BM_FrameStack_MixedPush)->Range(8, 2048);

static void
BM_Deque_MixedPush(benchmark::State& state)
{
    Frame frame1(1, 1), frame2(2, 2);
    for (auto _ : state) {
        std::deque<std::reference_wrapper<Frame>> deque;
        for (int64_t i = 0; i < state.range(0); ++i) {
            if (i % 2 == 0) {
                deque.push_back(std::ref(frame1));
            } else {
                deque.push_front(std::ref(frame2));
            }
        }
        benchmark::DoNotOptimize(deque);
    }
}
BENCHMARK(BM_Deque_MixedPush)->Range(8, 2048);

// ============================================================================
// Benchmarks: Pop Back
// ============================================================================

static void
BM_FrameStack_PopBack(benchmark::State& state)
{
    Frame frame(1, 1);
    for (auto _ : state) {
        state.PauseTiming();
        static FrameStack stack;
        stack.clear();
        for (int64_t i = 0; i < state.range(0); ++i) {
            stack.push_back(std::ref(frame));
        }
        state.ResumeTiming();

        while (!stack.empty()) {
            stack.pop_back();
        }
        benchmark::DoNotOptimize(stack);
    }
}
BENCHMARK(BM_FrameStack_PopBack)->Range(8, 2048);

static void
BM_Deque_PopBack(benchmark::State& state)
{
    Frame frame(1, 1);
    for (auto _ : state) {
        state.PauseTiming();
        std::deque<std::reference_wrapper<Frame>> deque;
        for (int64_t i = 0; i < state.range(0); ++i) {
            deque.push_back(std::ref(frame));
        }
        state.ResumeTiming();

        while (!deque.empty()) {
            deque.pop_back();
        }
        benchmark::DoNotOptimize(deque);
    }
}
BENCHMARK(BM_Deque_PopBack)->Range(8, 2048);

// ============================================================================
// Benchmarks: Pop Front
// ============================================================================

static void
BM_FrameStack_PopFront(benchmark::State& state)
{
    Frame frame(1, 1);
    for (auto _ : state) {
        state.PauseTiming();
        static FrameStack stack;
        stack.clear();
        for (int64_t i = 0; i < state.range(0); ++i) {
            stack.push_back(std::ref(frame));
        }
        state.ResumeTiming();

        while (!stack.empty()) {
            stack.pop_front();
        }
        benchmark::DoNotOptimize(stack);
    }
}
BENCHMARK(BM_FrameStack_PopFront)->Range(8, 2048);

static void
BM_Deque_PopFront(benchmark::State& state)
{
    Frame frame(1, 1);
    for (auto _ : state) {
        state.PauseTiming();
        std::deque<std::reference_wrapper<Frame>> deque;
        for (int64_t i = 0; i < state.range(0); ++i) {
            deque.push_back(std::ref(frame));
        }
        state.ResumeTiming();

        while (!deque.empty()) {
            deque.pop_front();
        }
        benchmark::DoNotOptimize(deque);
    }
}
BENCHMARK(BM_Deque_PopFront)->Range(8, 2048);

// ============================================================================
// Benchmarks: Random Access
// ============================================================================

static void
BM_FrameStack_RandomAccess(benchmark::State& state)
{
    Frame frame(1, 1);
    static FrameStack stack;
    stack.clear();
    for (int64_t i = 0; i < state.range(0); ++i) {
        stack.push_back(std::ref(frame));
    }

    for (auto _ : state) {
        int64_t sum = 0;
        for (int64_t i = 0; i < state.range(0); ++i) {
            auto f = stack[i];
            sum += f.get().name;
        }
        benchmark::DoNotOptimize(sum);
    }
}
BENCHMARK(BM_FrameStack_RandomAccess)->Range(8, 2048);

static void
BM_Deque_RandomAccess(benchmark::State& state)
{
    Frame frame(1, 1);
    std::deque<std::reference_wrapper<Frame>> deque;
    for (int64_t i = 0; i < state.range(0); ++i) {
        deque.push_back(std::ref(frame));
    }

    for (auto _ : state) {
        int64_t sum = 0;
        for (int64_t i = 0; i < state.range(0); ++i) {
            auto& f = deque[i];
            sum += f.get().name;
        }
        benchmark::DoNotOptimize(sum);
    }
}
BENCHMARK(BM_Deque_RandomAccess)->Range(8, 2048);

// ============================================================================
// Benchmarks: Forward Iteration
// ============================================================================

static void
BM_FrameStack_ForwardIteration(benchmark::State& state)
{
    Frame frame(1, 1);
    static FrameStack stack;
    stack.clear();
    for (int64_t i = 0; i < state.range(0); ++i) {
        stack.push_back(std::ref(frame));
    }

    for (auto _ : state) {
        int64_t sum = 0;
        for (auto f : stack) {
            sum += f.get().name;
        }
        benchmark::DoNotOptimize(sum);
    }
}
BENCHMARK(BM_FrameStack_ForwardIteration)->Range(8, 2048);

static void
BM_Deque_ForwardIteration(benchmark::State& state)
{
    Frame frame(1, 1);
    std::deque<std::reference_wrapper<Frame>> deque;
    for (int64_t i = 0; i < state.range(0); ++i) {
        deque.push_back(std::ref(frame));
    }

    for (auto _ : state) {
        int64_t sum = 0;
        for (auto& f : deque) {
            sum += f.get().name;
        }
        benchmark::DoNotOptimize(sum);
    }
}
BENCHMARK(BM_Deque_ForwardIteration)->Range(8, 2048);

// ============================================================================
// Benchmarks: Reverse Iteration
// ============================================================================

static void
BM_FrameStack_ReverseIteration(benchmark::State& state)
{
    Frame frame(1, 1);
    static FrameStack stack;
    stack.clear();
    for (int64_t i = 0; i < state.range(0); ++i) {
        stack.push_back(std::ref(frame));
    }

    for (auto _ : state) {
        int64_t sum = 0;
        for (auto it = stack.rbegin(); it != stack.rend(); ++it) {
            sum += (*it).get().name;
        }
        benchmark::DoNotOptimize(sum);
    }
}
BENCHMARK(BM_FrameStack_ReverseIteration)->Range(8, 2048);

static void
BM_Deque_ReverseIteration(benchmark::State& state)
{
    Frame frame(1, 1);
    std::deque<std::reference_wrapper<Frame>> deque;
    for (int64_t i = 0; i < state.range(0); ++i) {
        deque.push_back(std::ref(frame));
    }

    for (auto _ : state) {
        int64_t sum = 0;
        for (auto it = deque.rbegin(); it != deque.rend(); ++it) {
            sum += (*it).get().name;
        }
        benchmark::DoNotOptimize(sum);
    }
}
BENCHMARK(BM_Deque_ReverseIteration)->Range(8, 2048);

// ============================================================================
// Benchmarks: Clear and Reuse
// ============================================================================

static void
BM_FrameStack_ClearAndReuse(benchmark::State& state)
{
    Frame frame(1, 1);
    static FrameStack stack;
    stack.clear();

    for (auto _ : state) {
        for (int64_t i = 0; i < state.range(0); ++i) {
            stack.push_back(std::ref(frame));
        }
        stack.clear();
        benchmark::DoNotOptimize(stack);
    }
}
BENCHMARK(BM_FrameStack_ClearAndReuse)->Range(8, 2048);

static void
BM_Deque_ClearAndReuse(benchmark::State& state)
{
    Frame frame(1, 1);
    std::deque<std::reference_wrapper<Frame>> deque;

    for (auto _ : state) {
        for (int64_t i = 0; i < state.range(0); ++i) {
            deque.push_back(std::ref(frame));
        }
        deque.clear();
        benchmark::DoNotOptimize(deque);
    }
}
BENCHMARK(BM_Deque_ClearAndReuse)->Range(8, 2048);

// ============================================================================
// Main
// ============================================================================

BENCHMARK_MAIN();
