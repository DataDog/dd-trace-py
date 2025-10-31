#include <gtest/gtest.h>
#include <pybind11/embed.h>

// Global GoogleTest environment to manage a single embedded Python
// interpreter lifecycle for the entire native test process. This avoids repeated
// init/fini across tests, reducing Valgrind "still reachable" noise from
// CPython/pybind11 internals and improving stability when sanitizers are used.

namespace py = pybind11;

class PyEnvEnv : public ::testing::Environment
{
  public:
    // Using SetUp/TearDown ensures order with other global test environments.
    void SetUp() override
    {
        if (!Py_IsInitialized()) {
            py::initialize_interpreter();
        }
    }

    void TearDown() override
    {
        if (Py_IsInitialized()) {
            py::finalize_interpreter();
        }
    }
};

// Register the global environment before tests run.
// This pointer is intentionally leaked by GoogleTest until process exit.
// NOLINTNEXTLINE(cppcoreguidelines-avoid-non-const-global-variables)
static ::testing::Environment* const kPyEnvEnv = ::testing::AddGlobalTestEnvironment(new PyEnvEnv());
