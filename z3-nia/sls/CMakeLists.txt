# HEAD NIA sources are embedded as objects in the Z3 shell. Do not link libz3:
# all Z3 C API symbols resolve against that shell's own API/core objects.
add_library(nia_ls_embedded OBJECT
  frontend/nia_frontend.cpp
  frontend/nia_import.cpp
  src/nia_omt_cutset.cpp
  src/nia_omt_model_bridge.cpp
  src/nia_omt_core_neighborhood.cpp
  src/nia_omt_convex_exchange.cpp
  src/nia_omt_objective_slice.cpp
  src/nia_omt_optimality.cpp
  src/nia_omt_unbounded.cpp
  src/nia_omt_bilinear.cpp
  src/nia_omt_feasibility.cpp
  src/nia_omt_repair.cpp
  src/nia_omt_optimize.cpp
  src/nia_omt_first.cpp
  src/nia_omt_input.cpp
  src/nia_build_instance.cpp
  src/nia_initialize.cpp
  src/nia_linear_initialize.cpp
  src/nia_restart_repair.cpp
  src/nia_ls.cpp
  src/nia_print.cpp
  src/call.cpp
)
target_compile_definitions(nia_ls_embedded PRIVATE NIA_LS_EMBEDDED)
target_include_directories(nia_ls_embedded
  PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/frontend"
  PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src"
          "${PROJECT_SOURCE_DIR}/src/api"
          "${PROJECT_SOURCE_DIR}/src/api/c++"
)
set_target_properties(nia_ls_embedded PROPERTIES
  # HEAD's Makefile uses the compiler's GNU++17 executable profile. This
  # component is linked into the shell, never into shared libz3.
  CXX_STANDARD 17
  CXX_STANDARD_REQUIRED ON
  CXX_EXTENSIONS ON
  POSITION_INDEPENDENT_CODE OFF)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  # Match HEAD's -w recipe and retain its assertions in a Release Z3 build.
  target_compile_options(nia_ls_embedded PRIVATE -g -w -UNDEBUG)
endif()
set(_nia_gmp_root "${GMP_ROOT}")
if(NOT _nia_gmp_root AND DEFINED ENV{GMP_ROOT})
  set(_nia_gmp_root "$ENV{GMP_ROOT}")
endif()
if(NOT _nia_gmp_root AND EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/../toolchain/gmp/include/gmpxx.h")
  set(_nia_gmp_root "${CMAKE_CURRENT_SOURCE_DIR}/../toolchain/gmp")
endif()
if(_nia_gmp_root)
  set(NIA_GMP_INCLUDE_DIR "${_nia_gmp_root}/include")
  set(NIA_GMPXX_LIBRARY "${_nia_gmp_root}/lib/libgmpxx.a")
  set(NIA_GMP_LIBRARY "${_nia_gmp_root}/lib/libgmp.a")
  foreach(_nia_gmp_file "${NIA_GMP_INCLUDE_DIR}/gmpxx.h" "${NIA_GMP_INCLUDE_DIR}/gmp.h" "${NIA_GMPXX_LIBRARY}" "${NIA_GMP_LIBRARY}")
    if(NOT EXISTS "${_nia_gmp_file}")
      message(FATAL_ERROR "NIA GMP_ROOT is incomplete: ${_nia_gmp_file}")
    endif()
  endforeach()
else()
  find_path(NIA_GMP_INCLUDE_DIR gmpxx.h REQUIRED)
  find_library(NIA_GMPXX_LIBRARY gmpxx REQUIRED)
  find_library(NIA_GMP_LIBRARY gmp REQUIRED)
endif()
target_include_directories(nia_ls_embedded PRIVATE "${NIA_GMP_INCLUDE_DIR}")
target_link_libraries(nia_ls_embedded INTERFACE "${NIA_GMPXX_LIBRARY}" "${NIA_GMP_LIBRARY}")
