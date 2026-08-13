cmake_minimum_required(VERSION 3.14)
project(srsran_er_protobuf_probe LANGUAGES CXX)

if(NOT DEFINED SRSRAN_REPOSITORY_ROOT)
  message(FATAL_ERROR "SRSRAN_REPOSITORY_ROOT must point to the repository")
endif()

list(APPEND CMAKE_MODULE_PATH "${SRSRAN_REPOSITORY_ROOT}/cmake/modules")
find_package(Protobuf REQUIRED)
include(CheckProtobufToolchain)
srsran_check_protobuf_toolchain()
