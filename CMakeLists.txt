# SPDX-License-Identifier: GPL-3.0-only
cmake_minimum_required(VERSION 3.20)
project(hamsdr_next VERSION 0.5.4 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
include(CheckPIESupported)
check_pie_supported()

option(HAMSDR_ENABLE_SANITIZERS "Enable address and undefined-behavior sanitizers" OFF)

find_package(PkgConfig REQUIRED)
pkg_check_modules(FFTW3F REQUIRED IMPORTED_TARGET fftw3f)
find_package(Threads REQUIRED)

add_library(hamsdr_core
    src/iq.cpp
    src/rtl_tcp_client.cpp
    src/spectrum.cpp
    src/receiver.cpp
)
target_include_directories(hamsdr_core PUBLIC include)
target_link_libraries(hamsdr_core PUBLIC PkgConfig::FFTW3F Threads::Threads)
target_compile_options(hamsdr_core PRIVATE -Wall -Wextra -Wpedantic -Wconversion -Wshadow)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(hamsdr_core PUBLIC -fstack-protector-strong)
    target_link_options(hamsdr_core PUBLIC -Wl,-z,relro,-z,now)
endif()

if(HAMSDR_ENABLE_SANITIZERS AND CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(hamsdr_core PUBLIC -fsanitize=address,undefined -fno-omit-frame-pointer)
    target_link_options(hamsdr_core PUBLIC -fsanitize=address,undefined)
endif()

add_executable(hamsdr-next-probe src/main.cpp)
target_link_libraries(hamsdr-next-probe PRIVATE hamsdr_core)
add_executable(hamsdr-next-engine src/engine.cpp)
target_link_libraries(hamsdr-next-engine PRIVATE hamsdr_core)

enable_testing()
add_executable(hamsdr-core-tests tests/core_tests.cpp)
target_link_libraries(hamsdr-core-tests PRIVATE hamsdr_core)
add_test(NAME hamsdr-core-tests COMMAND hamsdr-core-tests)
add_executable(hamsdr-receiver-tests tests/receiver_tests.cpp)
target_link_libraries(hamsdr-receiver-tests PRIVATE hamsdr_core)
add_test(NAME hamsdr-receiver-tests COMMAND hamsdr-receiver-tests)
add_executable(hamsdr-transport-tests tests/transport_tests.cpp)
target_link_libraries(hamsdr-transport-tests PRIVATE hamsdr_core)
add_test(NAME hamsdr-transport-tests COMMAND hamsdr-transport-tests)
set_tests_properties(hamsdr-transport-tests PROPERTIES TIMEOUT 20)
add_executable(hamsdr-audio-processing-tests tests/audio_processing_tests.cpp)
target_link_libraries(hamsdr-audio-processing-tests PRIVATE hamsdr_core)
add_test(NAME hamsdr-audio-processing-tests COMMAND hamsdr-audio-processing-tests)
