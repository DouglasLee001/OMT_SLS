# Distributed under the OSI-approved BSD 3-Clause License.  See accompanying
# file Copyright.txt or https://cmake.org/licensing for details.

cmake_minimum_required(VERSION 3.5)

file(MAKE_DIRECTORY
  "/home/lbh/OMT/competitor/z3-5.1.0/source/examples/c++"
  "/home/lbh/OMT/competitor/z3-5.1.0/build/examples/cpp_example_build_dir"
  "/home/lbh/OMT/competitor/z3-5.1.0/build/examples/cpp_example-prefix"
  "/home/lbh/OMT/competitor/z3-5.1.0/build/examples/cpp_example-prefix/tmp"
  "/home/lbh/OMT/competitor/z3-5.1.0/build/examples/cpp_example-prefix/src/cpp_example-stamp"
  "/home/lbh/OMT/competitor/z3-5.1.0/build/examples/cpp_example-prefix/src"
  "/home/lbh/OMT/competitor/z3-5.1.0/build/examples/cpp_example-prefix/src/cpp_example-stamp"
)

set(configSubDirs )
foreach(subDir IN LISTS configSubDirs)
    file(MAKE_DIRECTORY "/home/lbh/OMT/competitor/z3-5.1.0/build/examples/cpp_example-prefix/src/cpp_example-stamp/${subDir}")
endforeach()
if(cfgdir)
  file(MAKE_DIRECTORY "/home/lbh/OMT/competitor/z3-5.1.0/build/examples/cpp_example-prefix/src/cpp_example-stamp${cfgdir}") # cfgdir has leading slash
endif()
