#pragma once

#include <algorithm>
#include <array>
#include <charconv>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <type_traits>
#include <utility>
#include <variant>

class TaskName
{
  public:
    [[nodiscard]] static TaskName from_literal(std::string value) { return TaskName(std::move(value)); }

    [[nodiscard]] static TaskName from_asyncio_task_id(std::uint64_t id) { return TaskName(AsyncioTaskId{ id }); }

    [[nodiscard]] static TaskName from_greenlet_id(std::uint64_t id) { return TaskName(GreenletId{ id }); }

    [[nodiscard]] static TaskName from_gevent_name(std::string_view name)
    {
        auto maybe_id = parse_prefixed_uint(name, "Greenlet-");
        if (maybe_id) {
            return from_greenlet_id(*maybe_id);
        }
        return from_literal(std::string(name));
    }

    // The string_view passed to callback is valid only for the callback duration.
    template<typename Callback>
    void visit_string(Callback&& callback) const
    {
        std::visit(
          [&](const auto& v) {
              using T = std::decay_t<decltype(v)>;
              if constexpr (std::is_same_v<T, std::string>) {
                  callback(std::string_view(v));
              } else if constexpr (std::is_same_v<T, AsyncioTaskId>) {
                  visit_prefixed_number("Task-", v.value, callback);
              } else if constexpr (std::is_same_v<T, GreenletId>) {
                  visit_prefixed_number("Greenlet-", v.value, callback);
              }
          },
          storage_);
    }

  private:
    struct AsyncioTaskId
    {
        std::uint64_t value;
    };
    struct GreenletId
    {
        std::uint64_t value;
    };

    using Storage = std::variant<std::string, AsyncioTaskId, GreenletId>;

    explicit TaskName(std::string s)
      : storage_(std::move(s))
    {
    }
    explicit TaskName(AsyncioTaskId id)
      : storage_(id)
    {
    }
    explicit TaskName(GreenletId id)
      : storage_(id)
    {
    }

    template<std::size_t PrefixSize, typename Callback>
    static void visit_prefixed_number(const char (&prefix)[PrefixSize], std::uint64_t value, Callback&& callback)
    {
        constexpr std::size_t prefix_size = PrefixSize - 1;
        constexpr std::size_t max_digits = std::numeric_limits<std::uint64_t>::digits10 + 1;
        std::array<char, prefix_size + max_digits> buffer{};

        auto digits_begin = std::copy_n(prefix, prefix_size, buffer.begin());

        auto [ptr, ec] = std::to_chars(std::to_address(digits_begin), buffer.data() + buffer.size(), value);
        if (ec != std::errc{}) {
            callback(std::string_view("<unknown>"));
            return;
        }

        callback(std::string_view(buffer.data(), static_cast<std::size_t>(ptr - buffer.data())));
    }

    [[nodiscard]] static std::optional<std::uint64_t> parse_prefixed_uint(std::string_view value,
                                                                          std::string_view prefix)
    {
        if (value.size() <= prefix.size() || value.compare(0, prefix.size(), prefix) != 0) {
            return std::nullopt;
        }

        const char* suffix_begin = value.data() + prefix.size();
        const char* suffix_end = value.data() + value.size();
        std::uint64_t result = 0;
        auto [ptr, ec] = std::from_chars(suffix_begin, suffix_end, result);
        if (ec != std::errc{} || ptr != suffix_end) {
            return std::nullopt;
        }

        return result;
    }

    Storage storage_;
};
