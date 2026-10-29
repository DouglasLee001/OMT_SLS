#pragma once
#include <functional>

namespace nia { namespace embedded {
bool requested(int argc, char* const argv[]) noexcept;
int run_frontend(int argc, char* argv[]);
// Calls the original HEAD CLI implementation in this executable. The offset
// includes SMT2 reading/conversion; HEAD already charges import time to timeout.
// The optional hook runs after HEAD validates every execution flag and before
// input is opened or any user output is written. It may throw to reject I/O.
int run_native_cli(int argc, char* argv[], double external_import_seconds = 0,
                   const std::function<void()>& before_input = {});
}}
