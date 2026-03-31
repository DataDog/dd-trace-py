/*
 * MIT License

Copyright (c) 2016/2017 Eric Niebler, slightly adapted by Peter Sommerlad

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
 */
#pragma once

#if __has_include(<experimental/scope>)

#include <experimental/scope>

#if __cpp_lib_experimental_scope >= 201902
// if available use the STL implementation
#define USE_STD_EXPERIMENTAL_RESOURCE
#endif
#endif

#ifdef USE_STD_EXPERIMENTAL_RESOURCE
using std::experimental::make_unique_resource_checked;
using std::experimental::scope_exit;
using std::experimental::scope_fail;
using std::experimental::scope_success;
using std::experimental::unique_resource;
#else

#include <exception> // for std::uncaught_exceptions
#include <functional>
#include <limits> // for maxint
#include <type_traits>
#include <utility>

// modeled slightly after Andrescu's talk and article(s)

// contribution by (c) Eric Niebler 2016, adapted by (c) 2017 Peter Sommerlad
namespace detail {
namespace hidden {

// should this helper be standardized? // write testcase where recognizable.
template<typename T>
constexpr std::conditional_t<std::is_nothrow_move_assignable_v<T>, T&&, T const&>
move_assign_if_noexcept(T& x) noexcept
{
    return std::move(x);
}

template<class T>
constexpr typename std::
  conditional<!std::is_nothrow_move_constructible_v<T> && std::is_copy_constructible_v<T>, const T&, T&&>::type
  move_if_noexcept(T& x) noexcept;

} // namespace hidden

template<typename T>
class box
{
    [[no_unique_address]] T value;
    explicit box(T const& t) noexcept(noexcept(T(t)))
      : value(t)
    {
    }
    explicit box(T&& t) noexcept(noexcept(T(std::move_if_noexcept(t))))
      : value(std::move_if_noexcept(t))
    {
    }

  public:
    template<typename TT, typename GG>
        requires std::is_constructible_v<T, TT>
    explicit box(TT&& t, GG&& guard) noexcept(noexcept(box(static_cast<T&&>(t))))
      : box(std::forward<TT>(t))
    {
        guard.release();
    }
    box() = default;

    T& get() noexcept { return value; }
    T const& get() const noexcept { return value; }
    T&& move() noexcept { return std::move(value); }
    void reset(T const& t) noexcept(noexcept(value = t)) { value = t; }
    void reset(T&& t) noexcept(noexcept(value = hidden::move_assign_if_noexcept(t)))
    {
        value = hidden::move_assign_if_noexcept(t);
    }
};

template<typename T>
class box<T&>
{
    std::reference_wrapper<T> value;

  public:
    template<typename TT, typename GG>
        requires std::is_convertible_v<TT, T&>
    box(TT&& t, GG&& guard) noexcept(noexcept(static_cast<T&>(static_cast<TT&&>(t))))
      : value(static_cast<T&>(t))
    {
        guard.release();
    }
    T& get() const noexcept { return value.get(); }
    T& move() const noexcept { return get(); }
    void reset(T& t) noexcept { value = std::ref(t); }
};

// new policy-based exception proof design by Eric Niebler
struct on_exit_policy
{
    bool execute_{ true };

    void release() noexcept { execute_ = false; }

    bool should_execute() const noexcept { return execute_; }
};

struct on_fail_policy
{
    int ec_{ std::uncaught_exceptions() };

    void release() noexcept { ec_ = std::numeric_limits<int>::max(); }

    bool should_execute() const noexcept { return ec_ < std::uncaught_exceptions(); }
};

struct on_success_policy
{
    int ec_{ std::uncaught_exceptions() };

    void release() noexcept { ec_ = -1; }

    bool should_execute() const noexcept { return ec_ >= std::uncaught_exceptions(); }
};
} // namespace detail

template<class EF, class Policy = detail::on_exit_policy>
class basic_scope_exit; // silence brain dead clang warning -Wmismatched-tags

// PS: It would be nice if just the following would work in C++17
// PS: however, we need a real class for template argument deduction
// PS: and a deduction guide, because the ctors are partially instantiated
// template<class EF>
// using scope_exit = basic_scope_exit<EF, detail::on_exit_policy>;

template<class EF>
struct [[nodiscard]] scope_exit : basic_scope_exit<EF, detail::on_exit_policy>
{
    using basic_scope_exit<EF, detail::on_exit_policy>::basic_scope_exit;
};

template<class EF>
scope_exit(EF) -> scope_exit<EF>;

// template<class EF>
// using scope_fail = basic_scope_exit<EF, detail::on_fail_policy>;

template<class EF>
struct scope_fail : basic_scope_exit<EF, detail::on_fail_policy>
{
    using basic_scope_exit<EF, detail::on_fail_policy>::basic_scope_exit;
};

template<class EF>
scope_fail(EF) -> scope_fail<EF>;

// template<class EF>
// using scope_success = basic_scope_exit<EF, detail::on_success_policy>;

template<class EF>
struct scope_success : basic_scope_exit<EF, detail::on_success_policy>
{
    using basic_scope_exit<EF, detail::on_success_policy>::basic_scope_exit;
};

template<class EF>
scope_success(EF) -> scope_success<EF>;

namespace detail {
// DETAIL:
template<class Policy, class EF>
auto
_make_guard(EF&& ef)
{
    return basic_scope_exit<std::decay_t<EF>, Policy>(std::forward<EF>(ef));
}
struct _empty_scope_exit
{
    void release() const noexcept {}
};

} // namespace detail

// Requires: EF is Callable
// Requires: EF is nothrow MoveConstructible OR CopyConstructible
template<class EF, class Policy /*= on_exit_policy*/>
class [[nodiscard]] basic_scope_exit : Policy
{
    static_assert(std::is_invocable_v<EF>, "scope guard must be callable");
    [[no_unique_address]] detail::box<EF> exit_function;

    static auto _make_failsafe(std::true_type, const void*) { return detail::_empty_scope_exit{}; }
    static auto _make_failsafe(std::true_type, void (*)()) { return detail::_empty_scope_exit{}; }
    template<typename Fn>
    static auto _make_failsafe(std::false_type, Fn* fn)
    {
        return basic_scope_exit<Fn&, Policy>(*fn);
    }
    template<typename EFP>
    using _ctor_from = std::is_constructible<detail::box<EF>, EFP, detail::_empty_scope_exit>;
    template<typename EFP>
    using _noexcept_ctor_from =
      std::bool_constant<noexcept(detail::box<EF>(std::declval<EFP>(), detail::_empty_scope_exit{}))>;

  public:
    template<typename EFP>
        requires _ctor_from<EFP>::value
    explicit basic_scope_exit(EFP&& ef) noexcept(_noexcept_ctor_from<EFP>::value)
      : exit_function(std::forward<EFP>(ef), _make_failsafe(_noexcept_ctor_from<EFP>{}, &ef))
    {
    }
    basic_scope_exit(basic_scope_exit&& that) noexcept(noexcept(detail::box<EF>(that.exit_function.move(), that)))
      : Policy(that)
      , exit_function(that.exit_function.move(), that)
    {
    }
    ~basic_scope_exit() noexcept(noexcept(exit_function.get()()))
    {
        if (this->should_execute())
            exit_function.get()();
    }
    basic_scope_exit(const basic_scope_exit&) = delete;
    basic_scope_exit& operator=(const basic_scope_exit&) = delete;
    basic_scope_exit& operator=(basic_scope_exit&&) = delete;

    using Policy::release;
};

template<class EF, class Policy>
void
swap(basic_scope_exit<EF, Policy>&, basic_scope_exit<EF, Policy>&) = delete;

template<typename R, typename D>
class unique_resource
{
    static_assert((std::is_move_constructible_v<R> && std::is_nothrow_move_constructible_v<R>) ||
                    std::is_copy_constructible_v<R>,
                  "resource must be nothrow_move_constructible or copy_constructible");
    static_assert((std::is_move_constructible_v<R> && std::is_nothrow_move_constructible_v<D>) ||
                    std::is_copy_constructible_v<D>,
                  "deleter must be nothrow_move_constructible or copy_constructible");

    static const unique_resource& this_; // never ODR used! Just for getting no_except() expr

    [[no_unique_address]] detail::box<R> resource{};
    [[no_unique_address]] detail::box<D> deleter{};
    bool execute_on_destruction{ false };

    static constexpr auto is_nothrow_delete_v =
      std::bool_constant<noexcept(std::declval<D&>()(std::declval<R&>()))>::value;

  public: // should be private
    template<typename RR, typename DD>
        requires std::is_constructible_v<detail::box<R>, RR, detail::_empty_scope_exit> &&
                   std::is_constructible_v<detail::box<D>, DD, detail::_empty_scope_exit>
    unique_resource(RR&& r, DD&& d, bool should_run) noexcept(
      noexcept(detail::box<R>(std::forward<RR>(r), detail::_empty_scope_exit{})) &&
      noexcept(detail::box<D>(std::forward<DD>(d), detail::_empty_scope_exit{})))
      : resource(std::forward<RR>(r), scope_exit([&] {
                     if (should_run)
                         d(r);
                 }))
      , deleter(std::forward<DD>(d), scope_exit([&, this] {
                    if (should_run)
                        d(get());
                }))
      , execute_on_destruction{ should_run }
    {
    }
    // need help in making the factory a nice friend...
    // the following two ICEs my g++ and gives compile errors about mismatche
    // exception spec on clang7
    //    template<typename RM, typename DM, typename S>
    //    friend
    //    auto make_unique_resource_checked(RM &&r, const S &invalid, DM &&d)
    //    noexcept(noexcept(make_unique_resource(std::forward<RM>(r),
    //    std::forward<DM>(d))));
    // the following as well:
    //    template<typename MR, typename MD, typename S>
    //    friend
    //	auto make_unique_resource_checked(MR &&r, const S &invalid, MD &&d)
    //    noexcept(std::is_nothrow_constructible_v<std::decay_t<MR>,MR> &&
    //    		std::is_nothrow_constructible_v<std::decay_t<MD>,MD>)
    //    ->unique_resource<std::decay_t<MR>,std::decay_t<MD>>;

  public:
    unique_resource() = default;

    template<typename RR, typename DD>
        requires std::is_constructible_v<detail::box<R>, RR, detail::_empty_scope_exit> &&
                   std::is_constructible_v<detail::box<D>, DD, detail::_empty_scope_exit>
    unique_resource(RR&& r,
                    DD&& d) noexcept(noexcept(detail::box<R>(std::forward<RR>(r), detail::_empty_scope_exit{})) &&
                                     noexcept(detail::box<D>(std::forward<DD>(d), detail::_empty_scope_exit{})))
      : resource(std::forward<RR>(r), scope_exit([&] { d(r); }))
      , deleter(std::forward<DD>(d), scope_exit([&, this] { d(get()); }))
      , execute_on_destruction{ true }
    {
    }
    unique_resource(unique_resource&& that) noexcept(
      noexcept(detail::box<R>(that.resource.move(), detail::_empty_scope_exit{})) &&
      noexcept(detail::box<D>(that.deleter.move(), detail::_empty_scope_exit{})))
      : resource(that.resource.move(), detail::_empty_scope_exit{})
      , deleter(that.deleter.move(), scope_exit([&, this] {
                    if (that.execute_on_destruction)
                        that.get_deleter()(get());
                    that.release();
                }))
      , execute_on_destruction(std::exchange(that.execute_on_destruction, false))
    {
    }

    unique_resource& operator=(unique_resource&& that) noexcept(is_nothrow_delete_v &&
                                                                std::is_nothrow_move_assignable_v<R> &&
                                                                std::is_nothrow_move_assignable_v<D>)
    {
        static_assert(std::is_nothrow_move_assignable<R>::value || std::is_copy_assignable<R>::value,
                      "The resource must be nothrow-move assignable, or copy assignable");
        static_assert(std::is_nothrow_move_assignable<D>::value || std::is_copy_assignable<D>::value,
                      "The deleter must be nothrow-move assignable, or copy assignable");
        if (&that != this) {
            reset();
            if constexpr (std::is_nothrow_move_assignable_v<detail::box<R>>)
                if constexpr (std::is_nothrow_move_assignable_v<detail::box<D>>) {
                    resource = std::move(that.resource);
                    deleter = std::move(that.deleter);
                } else {
                    deleter = _as_const(that.deleter);
                    resource = std::move(that.resource);
                }
            else if constexpr (std::is_nothrow_move_assignable_v<detail::box<D>>) {
                resource = _as_const(that.resource);
                deleter = std::move(that.deleter);
            } else {
                resource = _as_const(that.resource);
                deleter = _as_const(that.deleter);
            }
            execute_on_destruction = std::exchange(that.execute_on_destruction, false);
        }
        return *this;
    }
    ~unique_resource() // noexcept(is_nowthrow_delete_v) // removed deleter must
                       // not throw
    {
        reset();
    }

    void reset() noexcept
    {
        if (execute_on_destruction) {
            execute_on_destruction = false;
            get_deleter()(get());
        }
    }
    template<typename RR>
    auto reset(RR&& r) noexcept(noexcept(resource.reset(std::forward<RR>(r))))
      -> decltype(resource.reset(std::forward<RR>(r)), void())
    {
        auto&& guard = scope_fail([&, this] { get_deleter()(r); }); // -Wunused-variable on clang
        reset();
        resource.reset(std::forward<RR>(r));
        execute_on_destruction = true;
    }
    void release() noexcept { execute_on_destruction = false; }
    decltype(auto) get() const noexcept { return resource.get(); }
    decltype(auto) get_deleter() const noexcept { return deleter.get(); }
    template<typename RR = R>
        requires std::is_pointer_v<RR>
    auto operator->() const noexcept //(noexcept(detail::for_noexcept_on_copy_construction(this_.get())))
      -> decltype(get())
    {
        return get();
    }
    template<typename RR = R>
        requires std::is_pointer_v<RR> && (!std::is_void_v<std::remove_pointer_t<RR>>)
    std::add_lvalue_reference_t<std::remove_pointer_t<R>> operator*() const noexcept
    {
        return *get();
    }

    unique_resource& operator=(const unique_resource&) = delete;
    unique_resource(const unique_resource&) = delete;
};

template<typename R, typename D>
unique_resource(R, D) -> unique_resource<R, D>;
template<typename R, typename D>
unique_resource(R, D, bool) -> unique_resource<R, D>;

template<typename R, typename D, typename S>
[[nodiscard]] auto
make_unique_resource_checked(R&& r, const S& invalid, D&& d) noexcept(
  std::is_nothrow_constructible_v<std::decay_t<R>, R> &&
  std::is_nothrow_constructible_v<std::decay_t<D>, D>) -> unique_resource<std::decay_t<R>, std::decay_t<D>>
{
    bool const mustrelease(r == invalid);
    unique_resource resource{ std::forward<R>(r), std::forward<D>(d), !mustrelease };
    return resource;
}

// end of (c) Eric Niebler part

#undef USE_STD_EXPERIMENTAL_RESOURCE
#endif