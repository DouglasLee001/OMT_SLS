# Install script for directory: /home/lbh/OMT/competitor/z3-5.1.0/source/src/api/python

# Set the install prefix
if(NOT DEFINED CMAKE_INSTALL_PREFIX)
  set(CMAKE_INSTALL_PREFIX "/home/lbh/OMT/competitor/z3-5.1.0/install")
endif()
string(REGEX REPLACE "/$" "" CMAKE_INSTALL_PREFIX "${CMAKE_INSTALL_PREFIX}")

# Set the install configuration name.
if(NOT DEFINED CMAKE_INSTALL_CONFIG_NAME)
  if(BUILD_TYPE)
    string(REGEX REPLACE "^[^A-Za-z0-9_]+" ""
           CMAKE_INSTALL_CONFIG_NAME "${BUILD_TYPE}")
  else()
    set(CMAKE_INSTALL_CONFIG_NAME "Release")
  endif()
  message(STATUS "Install configuration: \"${CMAKE_INSTALL_CONFIG_NAME}\"")
endif()

# Set the component getting installed.
if(NOT CMAKE_INSTALL_COMPONENT)
  if(COMPONENT)
    message(STATUS "Install component: \"${COMPONENT}\"")
    set(CMAKE_INSTALL_COMPONENT "${COMPONENT}")
  else()
    set(CMAKE_INSTALL_COMPONENT)
  endif()
endif()

# Install shared libraries without execute permission?
if(NOT DEFINED CMAKE_INSTALL_SO_NO_EXE)
  set(CMAKE_INSTALL_SO_NO_EXE "1")
endif()

# Is this installation the result of a crosscompile?
if(NOT DEFINED CMAKE_CROSSCOMPILING)
  set(CMAKE_CROSSCOMPILING "FALSE")
endif()

# Set default install directory permissions.
if(NOT DEFINED CMAKE_OBJDUMP)
  set(CMAKE_OBJDUMP "/usr/bin/objdump")
endif()

if(CMAKE_INSTALL_COMPONENT STREQUAL "Unspecified" OR NOT CMAKE_INSTALL_COMPONENT)
  file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/lib/python3.12/site-packages/z3" TYPE FILE FILES
    "/home/lbh/OMT/competitor/z3-5.1.0/build/python/z3/__init__.py"
    "/home/lbh/OMT/competitor/z3-5.1.0/build/python/z3/z3.py"
    "/home/lbh/OMT/competitor/z3-5.1.0/build/python/z3/z3num.py"
    "/home/lbh/OMT/competitor/z3-5.1.0/build/python/z3/z3poly.py"
    "/home/lbh/OMT/competitor/z3-5.1.0/build/python/z3/z3printer.py"
    "/home/lbh/OMT/competitor/z3-5.1.0/build/python/z3/z3regex.py"
    "/home/lbh/OMT/competitor/z3-5.1.0/build/python/z3/z3rcf.py"
    "/home/lbh/OMT/competitor/z3-5.1.0/build/python/z3test.py"
    "/home/lbh/OMT/competitor/z3-5.1.0/build/python/z3/z3types.py"
    "/home/lbh/OMT/competitor/z3-5.1.0/build/python/z3/z3util.py"
    "/home/lbh/OMT/competitor/z3-5.1.0/build/python/z3/z3core.py"
    "/home/lbh/OMT/competitor/z3-5.1.0/build/python/z3/z3consts.py"
    "/home/lbh/OMT/competitor/z3-5.1.0/build/python/libz3.so.5.1"
    )
endif()

