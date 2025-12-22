// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <algorithm>
#include <cassert>
#include <optional>
#include <unordered_set>
#include <vector>

#include <echion/config.h>
#include <echion/frame.h>
#if PY_VERSION_HEX >= 0x030b0000
#include "echion/stack_chunk.h"
#endif // PY_VERSION_HEX >= 0x030b0000
#include <echion/errors.h>

// ----------------------------------------------------------------------------

class FrameStack
{
    // initial_capacity for the stack, shared between "front" and "back"
    static constexpr size_t initial_capacity = 2048;

  private:
    std::vector<std::optional<Frame::Ref>> buffer_;
    size_t start_; // Index of the first element
    size_t end_;   // Index one past the last element

    void grow()
    {
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
        // UB to pop from empty, like std::deque
        assert(!empty() && "pop_back on empty FrameStack");
        --end_;
    }

    void pop_front()
    {
        // UB to pop from empty, like std::deque
        assert(!empty() && "pop_front on empty FrameStack");
        ++start_;
    }

    Frame::Ref operator[](size_t index) { return *buffer_[start_ + index]; }
    Frame::Ref operator[](size_t index) const { return *buffer_[start_ + index]; }

    iterator begin() { return iterator(buffer_.begin() + start_); }
    iterator end() { return iterator(buffer_.begin() + end_); }
    const_iterator begin() const { return const_iterator(buffer_.begin() + start_); }
    const_iterator end() const { return const_iterator(buffer_.begin() + end_); }

    reverse_iterator rbegin() { return reverse_iterator(end()); }
    reverse_iterator rend() { return reverse_iterator(begin()); }
    const_reverse_iterator rbegin() const { return const_reverse_iterator(end()); }
    const_reverse_iterator rend() const { return const_reverse_iterator(begin()); }

    // ------------------------------------------------------------------------
    void render()
    {
        for (auto it = this->rbegin(); it != this->rend(); ++it) {
#if PY_VERSION_HEX >= 0x030c0000
            if ((*it).get().is_entry)
                // This is a shim frame so we skip it.
                continue;
#endif
            Renderer::get().render_frame((*it).get());
        }
    }
};

// ----------------------------------------------------------------------------

inline FrameStack python_stack;

// ----------------------------------------------------------------------------
static size_t
unwind_frame(PyObject* frame_addr, FrameStack& stack)
{
    std::unordered_set<PyObject*> seen_frames; // Used to detect cycles in the stack
    int count = 0;

    PyObject* current_frame_addr = frame_addr;
    while (current_frame_addr != NULL && stack.size() < max_frames) {
        if (seen_frames.find(current_frame_addr) != seen_frames.end())
            break;

        seen_frames.insert(current_frame_addr);

#if PY_VERSION_HEX >= 0x030b0000
        auto maybe_frame = Frame::read(reinterpret_cast<_PyInterpreterFrame*>(current_frame_addr),
                                       reinterpret_cast<_PyInterpreterFrame**>(&current_frame_addr));
#else
        auto maybe_frame = Frame::read(current_frame_addr, &current_frame_addr);
#endif
        if (!maybe_frame) {
            break;
        }

        if (maybe_frame->get().name == StringTable::C_FRAME) {
            continue;
        }

        stack.push_back(*maybe_frame);
        count++;
    }

    return count;
}

// ----------------------------------------------------------------------------
static void
unwind_python_stack(PyThreadState* tstate, FrameStack& stack)
{
    stack.clear();
#if PY_VERSION_HEX >= 0x030b0000
    if (stack_chunk == nullptr) {
        stack_chunk = std::make_unique<StackChunk>();
    }

    if (!stack_chunk->update(reinterpret_cast<_PyStackChunk*>(tstate->datastack_chunk))) {
        stack_chunk = nullptr;
    }
#endif

#if PY_VERSION_HEX >= 0x030d0000
    PyObject* frame_addr = reinterpret_cast<PyObject*>(tstate->current_frame);
#elif PY_VERSION_HEX >= 0x030b0000
    _PyCFrame cframe;
    _PyCFrame* cframe_addr = tstate->cframe;
    if (copy_type(cframe_addr, cframe))
        // TODO: Invalid frame
        return;

    PyObject* frame_addr = reinterpret_cast<PyObject*>(cframe.current_frame);
#else // Python < 3.11
    PyObject* frame_addr = reinterpret_cast<PyObject*>(tstate->frame);
#endif
    unwind_frame(frame_addr, stack);
}

// ----------------------------------------------------------------------------
static void
unwind_python_stack(PyThreadState* tstate)
{
    unwind_python_stack(tstate, python_stack);
}

// ----------------------------------------------------------------------------
class StackInfo
{
  public:
    StringTable::Key task_name;
    bool on_cpu;
    FrameStack stack;

    StackInfo(StringTable::Key task_name, bool on_cpu)
      : task_name(task_name)
      , on_cpu(on_cpu)
    {
    }
};
