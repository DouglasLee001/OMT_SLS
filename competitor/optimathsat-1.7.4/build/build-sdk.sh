#!/usr/bin/env bash
set -euo pipefail
task_base_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
task_sdk_dir="$task_base_dir/sources/optimathsat-1.7.4-linux-64-bit"
mkdir -p "$task_base_dir/install/bin" "$task_base_dir/install/lib" "$task_base_dir/install/include" "$task_base_dir/install/share" "$task_base_dir/build"
cp "$task_sdk_dir/lib/libmathsat.a" "$task_base_dir/install/lib/libmathsat.a"
cp "$task_sdk_dir/include/"*.h "$task_base_dir/install/include/"
cp "$task_sdk_dir/README.txt" "$task_sdk_dir/LICENSE.txt" "$task_sdk_dir/CREDITS.txt" "$task_sdk_dir/gpl-3.0.txt" "$task_sdk_dir/lgpl-3.0.txt" "$task_base_dir/install/share/"
cp "$task_sdk_dir/bin/optimathsat" "$task_base_dir/install/bin/optimathsat-official-prebuilt"
gcc -O2 -c "$task_base_dir/sources/main.c" -o "$task_base_dir/build/main.o"
g++ "$task_base_dir/build/main.o" "$task_base_dir/install/lib/libmathsat.a" -lgmpxx -lgmp -o "$task_base_dir/install/bin/optimathsat"
g++ "$task_sdk_dir/examples/optimization/api/api_example.cpp" -I"$task_base_dir/install/include" -L"$task_base_dir/install/lib" -lmathsat -lgmpxx -lgmp -lstdc++ -o "$task_base_dir/build/optimization-api-example"
