# Distributed under the OSI-approved BSD 3-Clause License.  See accompanying
# file Copyright.txt or https://cmake.org/licensing for details.

cmake_minimum_required(VERSION 3.5)

file(MAKE_DIRECTORY
  "/home/lbh/OMT/competitor/z3-5.1.0/source/examples/userPropagator"
  "/home/lbh/OMT/competitor/z3-5.1.0/build/examples/userPropagator_build_dir"
  "/home/lbh/OMT/competitor/z3-5.1.0/build/examples/userPropagator-prefix"
  "/home/lbh/OMT/competitor/z3-5.1.0/build/examples/userPropagator-prefix/tmp"
  "/home/lbh/OMT/competitor/z3-5.1.0/build/examples/userPropagator-prefix/src/userPropagator-stamp"
  "/home/lbh/OMT/competitor/z3-5.1.0/build/examples/userPropagator-prefix/src"
  "/home/lbh/OMT/competitor/z3-5.1.0/build/examples/userPropagator-prefix/src/userPropagator-stamp"
)

set(configSubDirs )
foreach(subDir IN LISTS configSubDirs)
    file(MAKE_DIRECTORY "/home/lbh/OMT/competitor/z3-5.1.0/build/examples/userPropagator-prefix/src/userPropagator-stamp/${subDir}")
endforeach()
if(cfgdir)
  file(MAKE_DIRECTORY "/home/lbh/OMT/competitor/z3-5.1.0/build/examples/userPropagator-prefix/src/userPropagator-stamp${cfgdir}") # cfgdir has leading slash
endif()
