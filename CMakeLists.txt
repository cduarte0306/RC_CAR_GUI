cmake_minimum_required(VERSION 3.15)

# Ensure CMake honors MSVC runtime selection via CMAKE_MSVC_RUNTIME_LIBRARY
# (Otherwise some dependencies may force /MT and cause LNK2038 mismatches.)
if(POLICY CMP0091)
    cmake_policy(SET CMP0091 NEW)
endif()

# Option to enable/disable CUDA - default ON for GPU-accelerated 3D processing
# NOTE: Requires CUDA 12.x - CUDA 13.x has Thrust/cccl header compatibility issues with Open3D's stdgpu
option(RC_CAR_USE_CUDA "Enable CUDA support in Open3D (requires CUDA 12.x)" ON)

# Option to use pre-built Open3D instead of building from source (saves ~30+ minutes)
# Set to path of Open3D install prefix (containing lib/cmake/Open3D/Open3DConfig.cmake)
# Example: -DOPEN3D_ROOT="C:/Open3D/install" or -DOPEN3D_ROOT="/opt/open3d"
set(OPEN3D_ROOT "" CACHE PATH "Path to pre-built Open3D installation (leave empty to build from source)")

# Validate CUDA version if CUDA is enabled
if(RC_CAR_USE_CUDA)
    if(DEFINED CMAKE_CUDA_COMPILER)
        # Extract version from compiler path or let CMake detect it
        message(STATUS "Using specified CUDA compiler: ${CMAKE_CUDA_COMPILER}")
    endif()
    project(rc_car_cpp VERSION 1.0.0 LANGUAGES CXX CUDA)
    
    # Verify we have CUDA 12.x (not 13.x which has compatibility issues)
    if(CMAKE_CUDA_COMPILER_VERSION VERSION_GREATER_EQUAL "13.0")
        message(WARNING "CUDA ${CMAKE_CUDA_COMPILER_VERSION} detected. CUDA 13.x has Thrust/cccl compatibility issues with Open3D's stdgpu.")
        message(WARNING "Recommend using CUDA 12.x or building without CUDA (-DRC_CAR_USE_CUDA=OFF)")
    elseif(CMAKE_CUDA_COMPILER_VERSION VERSION_LESS "12.0")
        message(WARNING "CUDA ${CMAKE_CUDA_COMPILER_VERSION} detected. CUDA 12.x is recommended for Open3D.")
    else()
        message(STATUS "CUDA ${CMAKE_CUDA_COMPILER_VERSION} - compatible with Open3D")
    endif()
else()
    project(rc_car_cpp VERSION 1.0.0 LANGUAGES CXX)
endif()

# Set C++ standard
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Keep MSVC runtime consistent across the whole build (Open3D + all its deps + our targets).
# We want dynamic CRT (/MD, /MDd) to avoid mixing /MT + /MD which causes LNK2038 and ODR issues.
if(MSVC)
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>DLL" CACHE STRING "" FORCE)
    set(STATIC_WINDOWS_RUNTIME OFF CACHE BOOL "Use dynamic (MD/MDd) Windows runtime" FORCE)
    set(JSONCPP_STATIC_WINDOWS_RUNTIME OFF CACHE BOOL "" FORCE)
    set(CURL_STATIC_CRT OFF CACHE BOOL "" FORCE)
endif()

# Prefer shared CUDA runtime to avoid linking both cudart_static and cudart (cudart64_*.dll import lib)
if(RC_CAR_USE_CUDA)
    set(CMAKE_CUDA_RUNTIME_LIBRARY Shared CACHE STRING "" FORCE)
endif()

# Store user's requested build type for our own targets
set(RC_CAR_BUILD_TYPE ${CMAKE_BUILD_TYPE})

# IMPORTANT: With single-config generators (Ninja/Makefiles), Open3D builds to lib/<CONFIG>/
# Since Open3D build is massive (~30min), we ALWAYS build everything as Release.
# For "Debug" builds, we add debug symbols (-Zi) to OUR code only.
# This avoids needing separate Open3D builds for Debug vs Release.
if(CMAKE_BUILD_TYPE STREQUAL "Debug")
    message(STATUS "Hybrid build: All libs=Release with debug symbols for RC_CAR targets")
    # Force Release for the entire build tree including Open3D
    set(CMAKE_BUILD_TYPE "Release" CACHE STRING "Build type" FORCE)
endif()

# Open3D build options - CUDA controlled by RC_CAR_USE_CUDA option
set(BUILD_CUDA_MODULE ${RC_CAR_USE_CUDA} CACHE BOOL "Build Open3D CUDA module" FORCE)
set(BUILD_PYTHON_MODULE OFF CACHE BOOL "Build Open3D Python module" FORCE)
set(BUILD_EXAMPLES OFF CACHE BOOL "Disable Open3D examples" FORCE)
set(BUILD_TESTS OFF CACHE BOOL "Disable Open3D tests" FORCE)
set(BUILD_GUI OFF CACHE BOOL "Disable Open3D GUI (avoids Qt dependency)" FORCE)
set(BUILD_JUPYTER OFF CACHE BOOL "Disable Open3D Jupyter support" FORCE)

set(BUILD_WEBRTC OFF CACHE BOOL "Disable Open3D WebRTC (avoids prebuilt binary mismatch on Windows)" FORCE)

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

# Pull and build Open3D (or use pre-built if OPEN3D_ROOT is set)
set(OPEN3D_SOURCE_DIR ${CMAKE_SOURCE_DIR}/external/open3d)
if(RC_CAR_USE_CUDA)
    set(BUILD_CUDA_MODULES ON CACHE BOOL "Build Open3D with CUDA support" FORCE)
else()
    set(BUILD_CUDA_MODULES OFF CACHE BOOL "Build Open3D with CUDA support" FORCE)
endif()

# Check if user provided a pre-built Open3D installation
if(OPEN3D_ROOT AND EXISTS "${OPEN3D_ROOT}")
    message(STATUS "Using pre-built Open3D from: ${OPEN3D_ROOT}")
    # Add the install prefix to CMake search path
    list(APPEND CMAKE_PREFIX_PATH "${OPEN3D_ROOT}")
    find_package(Open3D REQUIRED HINTS "${OPEN3D_ROOT}")
    if(Open3D_FOUND)
        message(STATUS "Found pre-built Open3D version: ${Open3D_VERSION}")
    else()
        message(FATAL_ERROR "Open3D not found at ${OPEN3D_ROOT}. Check that the path contains lib/cmake/Open3D/")
    endif()
elseif(EXISTS ${OPEN3D_SOURCE_DIR}/CMakeLists.txt)
    message(STATUS "Building Open3D from submodule: ${OPEN3D_SOURCE_DIR}")
    add_subdirectory(${OPEN3D_SOURCE_DIR})
else()
    message(WARNING "Open3D submodule not found, fetching release archive (this will take a while...)")
    FetchContent_Declare(
        open3d
        GIT_REPOSITORY https://github.com/isl-org/Open3D.git
        GIT_TAG main          # or a specific commit hash for reproducibility
        GIT_SHALLOW TRUE
    )
    FetchContent_MakeAvailable(open3d)
endif()

find_package(OpenGL REQUIRED)
find_package(OpenCV QUIET)
if(RC_CAR_USE_CUDA)
    find_package(CUDAToolkit REQUIRED)
endif()

# NOTE: CMAKE_BUILD_TYPE is now Release for everything.
# RC_CAR_BUILD_TYPE holds the user's original request (Debug or Release).
# We use RC_CAR_BUILD_TYPE to decide whether to add debug symbols to our targets.

# Debug compile/link flags for our targets (MSVC)
if(MSVC AND RC_CAR_BUILD_TYPE STREQUAL "Debug")
    message(STATUS "Adding debug symbols (/Zi /DEBUG) to RC_CAR targets")
endif()

# Core C++ library (static library for standalone testing)
add_library(rc_car_core STATIC
    cpp/src/math_operations.cpp
    cpp/src/renderer3d.cpp
)

target_include_directories(rc_car_core PUBLIC
    ${CMAKE_SOURCE_DIR}/cpp/include
)

# Apply debug flags to our core library
if(MSVC AND RC_CAR_BUILD_TYPE STREQUAL "Debug")
    target_compile_options(rc_car_core PRIVATE /Zi /Od)
    set_target_properties(rc_car_core PROPERTIES
        COMPILE_PDB_NAME "rc_car_core"
        COMPILE_PDB_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}"
    )
endif()

target_link_libraries(rc_car_core PUBLIC OpenGL::GL)

# Python module (DLL/shared library) - always Release for performance
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

# Apply debug flags to test executable
if(MSVC AND RC_CAR_BUILD_TYPE STREQUAL "Debug")
    target_compile_options(test_cpp PRIVATE /Zi /Od)
    target_link_options(test_cpp PRIVATE /DEBUG)
endif()

# Standalone C++ app for direct debugging
add_executable(rc_car_app
    cpp/src/main.cpp
)

target_link_libraries(rc_car_app PRIVATE rc_car_core OpenGL::GL)
target_link_libraries(rc_car_app PRIVATE Open3D::Open3D)
target_compile_definitions(rc_car_app PRIVATE RC_CAR_HAS_OPEN3D=1)
if(RC_CAR_OPEN3D_CPP_INCLUDE_DIR)
    target_include_directories(rc_car_app PRIVATE "${RC_CAR_OPEN3D_CPP_INCLUDE_DIR}")
endif()
if(OpenCV_FOUND)
    target_link_libraries(rc_car_app PRIVATE ${OpenCV_LIBS})
    target_include_directories(rc_car_app PRIVATE ${OpenCV_INCLUDE_DIRS})
    target_compile_definitions(rc_car_app PRIVATE RC_CAR_HAS_OPENCV=1)
endif()

# Apply debug flags to standalone app
if(MSVC AND RC_CAR_BUILD_TYPE STREQUAL "Debug")
    target_compile_options(rc_car_app PRIVATE /Zi /Od)
    target_link_options(rc_car_app PRIVATE /DEBUG)
endif()

# Installation rules
install(TARGETS rc_car_cpp
    LIBRARY DESTINATION python_modules
    RUNTIME DESTINATION python_modules
)

# Print configuration information
message(STATUS "RC Car C++ Module Configuration:")
message(STATUS "  C++ Standard: ${CMAKE_CXX_STANDARD}")
message(STATUS "  Build Type: ${RC_CAR_BUILD_TYPE} (Open3D: Release)")
message(STATUS "  Python Module Output: ${CMAKE_SOURCE_DIR}/python_modules")
if(OPEN3D_ROOT AND EXISTS "${OPEN3D_ROOT}")
    message(STATUS "  Open3D: Pre-built from ${OPEN3D_ROOT}")
else()
    message(STATUS "  Open3D: Building from source (use -DOPEN3D_ROOT=<path> to skip)")
    message(STATUS "  TIP: After build completes, run 'cmake --install build --prefix <install_path>'")
    message(STATUS "       Then use -DOPEN3D_ROOT=<install_path> for faster rebuilds")
endif()
