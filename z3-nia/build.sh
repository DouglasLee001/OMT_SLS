#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -x "$task_root/toolchain/cmake/bin/cmake" ]]; then
    export PATH="$task_root/toolchain/cmake/bin:$PATH"
    export LD_LIBRARY_PATH="$task_root/toolchain/cmake/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
cmake -S "$task_root/source" -B "$task_root/build" -G 'Unix Makefiles' \
    -DCMAKE_BUILD_TYPE=Release \
    -DZ3_INCLUDE_GIT_HASH=OFF -DZ3_INCLUDE_GIT_DESCRIBE=OFF \
    -DZ3_BUILD_EXECUTABLE=ON -DZ3_BUILD_LIBZ3_SHARED=ON \
    -DZ3_BUILD_TEST_EXECUTABLES=OFF -DZ3_ENABLE_EXAMPLE_TARGETS=OFF \
    -DZ3_BUILD_PYTHON_BINDINGS=OFF -DZ3_BUILD_JAVA_BINDINGS=OFF \
    -DZ3_BUILD_DOTNET_BINDINGS=OFF -DZ3_BUILD_DOCUMENTATION=OFF
make -C "$task_root/build" -j64

