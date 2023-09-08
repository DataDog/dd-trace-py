#pragma once

#include <exception>
#include <string>

using namespace std;

struct DatadogNativeException : public exception
{
  private:
    string message_;

  public:
    explicit DatadogNativeException(const std::string& message)
      : message_(message)
    {}

    [[nodiscard]] const char* what() const noexcept override { return message_.c_str(); }
};

struct ContextNotInitializedException : public DatadogNativeException
{
    explicit ContextNotInitializedException(const std::string& message)
      : DatadogNativeException(message)
    {}
};