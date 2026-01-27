cmake_minimum_required(VERSION 3.15)
project(rc_car_cpp VERSION 1.0.0 LANGUAGES CXX CUDA)

# Set C++ standard
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Add pybind11 (use submodule if available, otherwise fetch)
include(FetchContent)
set(PYBIND11_SOURCE_DIR ${CMAKE_SOURCE_DIR}/external/pybind11)
if(EXISTS ${PYBIND11_SOURCE_DIR}/CMakeLists.txt)
    add_subdirectory(${PYBIND11_SOURCE_DIR})
else()
    message(WARNING "pybind11 submodule not found, fetching release archive")
    FetchContent_Declare(
        pybind11
        URL https://github.com/pybind/pybind11/archive/refs/tags/v2.12.0.tar.gz
    )
    FetchContent_MakeAvailable(pybind11)
endif()

# Pull and build open3d with CUDA support
set(OPEN3D_SOURCE_DIR ${CMAKE_SOURCE_DIR}/external/open3d)
set(BUILD_CUDA_MODULES ON CACHE BOOL "Build Open3D with CUDA support" FORCE)

if(EXISTS ${OPEN3D_SOURCE_DIR}/CMakeLists.txt)
    add_subdirectory(${OPEN3D_SOURCE_DIR})
else()
    message(WARNING "Open3D submodule not found, fetching release archive")
    FetchContent_Declare(
        open3d
        URL https://github.com/isl-org/Open3D/archive/refs/tags/v0.17.0.tar.gz
    )
    FetchContent_MakeAvailable(open3d)
endif()

find_package(OpenGL REQUIRED)
find_package(OpenCV QUIET)
find_package(CUDA REQUIRED)

# Core C++ library (static library for standalone testing)
add_library(rc_car_core STATIC
    cpp/src/math_operations.cpp
    cpp/src/renderer3d.cpp
)

target_include_directories(rc_car_core PUBLIC
    ${CMAKE_SOURCE_DIR}/cpp/include
)

target_link_libraries(rc_car_core PUBLIC OpenGL::GL)

# Python module (DLL/shared library)
pybind11_add_module(rc_car_cpp
    cpp/src/bindings.cpp
)

target_link_libraries(rc_car_cpp PRIVATE rc_car_core OpenGL::GL)

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

# Standalone C++ app for direct debugging
add_executable(rc_car_app
    cpp/src/main.cpp
)

target_link_libraries(rc_car_app PRIVATE rc_car_core OpenGL::GL)
if(OpenCV_FOUND)
    target_link_libraries(rc_car_app PRIVATE ${OpenCV_LIBS})
    target_include_directories(rc_car_app PRIVATE ${OpenCV_INCLUDE_DIRS})
    target_compile_definitions(rc_car_app PRIVATE RC_CAR_HAS_OPENCV=1)
endif()

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
