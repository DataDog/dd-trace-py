// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2026 Datadog, Inc.

#pragma once

#include <cstddef>
#include <cstdint>
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

    [[nodiscard]] static TaskLabel from_asyncio_task_id(long long id)
    {
        TaskLabel label;
        label.kind_ = Kind::AsyncioTaskId;
        label.signed_id_ = id;
        return label;
    }

    [[nodiscard]] static TaskLabel from_greenlet_id(std::uint64_t id)
    {
        TaskLabel label;
        label.kind_ = Kind::GreenletId;
        label.unsigned_id_ = id;
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
        std::string formatted;
        switch (kind_) {
            case Kind::Literal:
                callback(std::string_view(literal_));
                return;
            case Kind::AsyncioTaskId:
                formatted = std::string("Task-") + std::to_string(signed_id_);
                callback(std::string_view(formatted));
                return;
            case Kind::GreenletId:
                formatted = std::string("Greenlet-") + std::to_string(unsigned_id_);
                callback(std::string_view(formatted));
                return;
            case Kind::Unknown:
            default:
                callback(std::string_view("<unknown>"));
                return;
        }
    }

  private:
    [[nodiscard]] static std::optional<std::uint64_t> parse_prefixed_uint(std::string_view value,
                                                                         std::string_view prefix)
    {
        if (value.size() <= prefix.size() || value.compare(0, prefix.size(), prefix) != 0) {
            return std::nullopt;
        }

        std::uint64_t result = 0;
        for (size_t i = prefix.size(); i < value.size(); i++) {
            char c = value[i];
            if (c < '0' || c > '9') {
                return std::nullopt;
            }

            auto digit = static_cast<std::uint64_t>(c - '0');
            if (result > (std::numeric_limits<std::uint64_t>::max() - digit) / 10) {
                return std::nullopt;
            }
            result = (result * 10) + digit;
        }

        return result;
    }

    Kind kind_ = Kind::Unknown;
    long long signed_id_ = 0;
    std::uint64_t unsigned_id_ = 0;
    std::string literal_;
};
