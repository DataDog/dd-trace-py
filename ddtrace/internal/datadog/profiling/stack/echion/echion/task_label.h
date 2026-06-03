// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2026 Datadog, Inc.

#pragma once

#include <array>
#include <charconv>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <optional>
#include <string>
#include <string_view>
#include <utility>

class TaskLabel
{
  public:
    enum class Kind : std::uint8_t
    {
        Unknown,
        Literal,
        AsyncioTaskId,
        GreenletId,
    };

    TaskLabel() = default;

    [[nodiscard]] static TaskLabel from_literal(std::string value)
    {
        TaskLabel label;
        label.kind_ = Kind::Literal;
        label.literal_ = std::move(value);
        return label;
    }

    [[nodiscard]] static TaskLabel from_asyncio_task_id(std::uint64_t id)
    {
        TaskLabel label;
        label.kind_ = Kind::AsyncioTaskId;
        label.numeric_id_ = id;
        return label;
    }

    [[nodiscard]] static TaskLabel from_greenlet_id(std::uint64_t id)
    {
        TaskLabel label;
        label.kind_ = Kind::GreenletId;
        label.numeric_id_ = id;
        return label;
    }

    [[nodiscard]] static TaskLabel from_gevent_name(std::string_view name)
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
        switch (kind_) {
            case Kind::Literal:
                callback(std::string_view(literal_));
                return;
            case Kind::AsyncioTaskId:
                visit_prefixed_number("Task-", numeric_id_, std::forward<Callback>(callback));
                return;
            case Kind::GreenletId:
                visit_prefixed_number("Greenlet-", numeric_id_, std::forward<Callback>(callback));
                return;
            case Kind::Unknown:
            default:
                callback(std::string_view("<unknown>"));
                return;
        }
    }

  private:
    template<std::size_t PrefixSize, typename Callback>
    static void visit_prefixed_number(const char (&prefix)[PrefixSize], std::uint64_t value, Callback&& callback)
    {
        constexpr std::size_t prefix_size = PrefixSize - 1;
        constexpr std::size_t max_digits = std::numeric_limits<std::uint64_t>::digits10 + 1;
        std::array<char, prefix_size + max_digits> buffer{};

        std::memcpy(buffer.data(), prefix, prefix_size);
        auto* begin = buffer.data();
        auto* digits_begin = begin + prefix_size;
        auto* end = begin + buffer.size();

        auto [ptr, ec] = std::to_chars(digits_begin, end, value);
        if (ec != std::errc{}) {
            callback(std::string_view("<unknown>"));
            return;
        }

        callback(std::string_view(begin, static_cast<std::size_t>(ptr - begin)));
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

    Kind kind_ = Kind::Unknown;
    std::uint64_t numeric_id_ = 0;
    std::string literal_;
};
