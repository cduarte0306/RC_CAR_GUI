cmake_minimum_required(VERSION 3.15)
project(rc_car_cpp VERSION 1.0.0 LANGUAGES CXX)

# Set C++ standard
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Add pybind11
add_subdirectory(external/pybind11)

# Core C++ library (static library for standalone testing)
add_library(rc_car_core STATIC
    cpp/src/math_operations.cpp
)

target_include_directories(rc_car_core PUBLIC
    ${CMAKE_SOURCE_DIR}/cpp/include
)

# Python module (DLL/shared library)
pybind11_add_module(rc_car_cpp
    cpp/src/bindings.cpp
)

target_link_libraries(rc_car_cpp PRIVATE rc_car_core)

target_include_directories(rc_car_cpp PRIVATE
    ${CMAKE_SOURCE_DIR}/cpp/include
)

# Set output directory for the Python module
set_target_properties(rc_car_cpp PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY ${CMAKE_SOURCE_DIR}/python_modules
    RUNTIME_OUTPUT_DIRECTORY ${CMAKE_SOURCE_DIR}/python_modules
)

# On Windows, set suffix to .pyd for Python extensions
if(WIN32)
    set_target_properties(rc_car_cpp PROPERTIES SUFFIX ".pyd")
endif()

# Standalone C++ test executable
add_executable(test_cpp
    cpp/tests/test_math_operations.cpp
)

target_link_libraries(test_cpp PRIVATE rc_car_core)

# Installation rules
install(TARGETS rc_car_cpp
    LIBRARY DESTINATION python_modules
    RUNTIME DESTINATION python_modules
)

# Print configuration information
message(STATUS "RC Car C++ Module Configuration:")
message(STATUS "  C++ Standard: ${CMAKE_CXX_STANDARD}")
message(STATUS "  Build Type: ${CMAKE_BUILD_TYPE}")
message(STATUS "  Python Module Output: ${CMAKE_SOURCE_DIR}/python_modules")
