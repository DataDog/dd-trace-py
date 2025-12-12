// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#include <type_traits>
#include <utility>

enum class ErrorKind
{
    Undefined,
    LookupError,
    FrameError,
    MirrorError,
    PyLongError,
    PyUnicodeError,
    StackChunkError,
    GenInfoError,
    TaskInfoError,
    TaskInfoGeneratorError,
    ThreadInfoError,
    CpuTimeError,
    LocationError,
};

template<typename T>
class [[nodiscard]] Result
{
  public:
    // Factories
    static Result ok(const T& v) { return Result(v); }
    static Result ok(T&& v) { return Result(std::move(v)); }
    static Result error(ErrorKind e) noexcept { return Result(e); }

    // Constructors
    Result(const T& v) noexcept(std::is_nothrow_copy_constructible<T>::value)
      : success_(true)
    {
        ::new (static_cast<void*>(std::addressof(value_))) T(v);
    }

    Result(T&& v) noexcept(std::is_nothrow_move_constructible<T>::value)
      : success_(true)
    {
        ::new (static_cast<void*>(std::addressof(value_))) T(std::move(v));
    }

    Result(ErrorKind e) noexcept
      : success_(false)
    {
        error_ = e;
    }

    // Destructor
    ~Result() { reset(); }

    // Copy ctor
    Result(const Result& other) noexcept(std::is_nothrow_copy_constructible<T>::value)
      : success_(other.success_)
    {
        if (success_) {
            ::new (static_cast<void*>(std::addressof(value_))) T(other.value_);
        } else {
            error_ = other.error_;
        }
    }

    // Move ctor
    Result(Result&& other) noexcept(std::is_nothrow_move_constructible<T>::value)
      : success_(other.success_)
    {
        if (success_) {
            ::new (static_cast<void*>(std::addressof(value_))) T(std::move(other.value_));
        } else {
            error_ = other.error_;
        }
    }

    // Copy assignment
    Result& operator=(const Result& other) noexcept(std::is_nothrow_copy_constructible<T>::value &&
                                                    std::is_nothrow_copy_assignable<T>::value)
    {
        if (this == &other)
            return *this;

        if (success_ && other.success_) {
            value_ = other.value_;
        } else if (success_ && !other.success_) {
            value_.~T();
            success_ = false;
            error_ = other.error_;
        } else if (!success_ && other.success_) {
            ::new (static_cast<void*>(std::addressof(value_))) T(other.value_);
            success_ = true;
        } else { // both errors
            error_ = other.error_;
        }
        return *this;
    }

    // Move assignment
    Result& operator=(Result&& other) noexcept(std::is_nothrow_move_constructible<T>::value &&
                                               std::is_nothrow_move_assignable<T>::value)
    {
        if (this == &other)
            return *this;

        if (success_ && other.success_) {
            value_ = std::move(other.value_);
        } else if (success_ && !other.success_) {
            value_.~T();
            success_ = false;
            error_ = other.error_;
        } else if (!success_ && other.success_) {
            ::new (static_cast<void*>(std::addressof(value_))) T(std::move(other.value_));
            success_ = true;
        } else { // both errors
            error_ = other.error_;
        }
        return *this;
    }

    // Observers
    explicit operator bool() const noexcept { return success_; }

    T& operator*() & { return value_; }
    const T& operator*() const& { return value_; }
    T&& operator*() && { return std::move(value_); }

    T* operator->() { return std::addressof(value_); }
    const T* operator->() const { return std::addressof(value_); }

    bool has_value() const noexcept { return success_; }

    // If in error, returns default_value
    template<typename U>
    T value_or(U&& default_value) const
    {
        return success_ ? value_ : static_cast<T>(std::forward<U>(default_value));
    }

    // Returns ErrorKind::Undefined when holding a value
    ErrorKind error() const noexcept { return success_ ? ErrorKind::Undefined : error_; }

  private:
    // Active member is tracked by success_
    union
    {
        ErrorKind error_;
        T value_;
    };
    bool success_;

    void reset() noexcept
    {
        if (success_) {
            value_.~T();
        }
    }
};

// Specialization for void
template<>
class [[nodiscard]] Result<void>
{
  public:
    static Result ok() noexcept { return Result(true, ErrorKind::Undefined); }
    static Result error(ErrorKind e) noexcept { return Result(false, e); }
    Result(ErrorKind e) noexcept
      : success_(false)
      , error_(e)
    {
    }

    explicit operator bool() const noexcept { return success_; }
    bool has_value() const noexcept { return success_; }

    // Returns ErrorKind::Undefined when success
    ErrorKind error() const noexcept { return success_ ? ErrorKind::Undefined : error_; }

  private:
    bool success_;
    ErrorKind error_;

    explicit Result(bool s, ErrorKind e) noexcept
      : success_(s)
      , error_(e)
    {
    }
};
