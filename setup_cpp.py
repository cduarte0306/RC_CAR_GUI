#!/usr/bin/env python3
"""
Setup script for building RC Car C++ extensions using CMake.

This script handles:
- CMake configuration and generation for multiple compilers (MSVC, GCC, Clang)
- Building the C++ DLL/pyd/so module using pybind11
- Cleaning build artifacts
- Running standalone C++ tests
- Cross-platform support for Windows, Linux, and macOS
"""

import os
import sys
import shutil
import subprocess
import argparse
from pathlib import Path

# Module configuration
MODULE_NAME = "rc_car_cpp"


class Colors:
    """ANSI color codes for terminal output"""
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


def print_header(message):
    """Print a formatted header message"""
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'=' * 60}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{message:^60}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'=' * 60}{Colors.ENDC}\n")


def print_success(message):
    """Print a success message"""
    print(f"{Colors.OKGREEN}[+] {message}{Colors.ENDC}")


def print_error(message):
    """Print an error message"""
    print(f"{Colors.FAIL}[x] {message}{Colors.ENDC}")


def print_info(message):
    """Print an info message"""
    print(f"{Colors.OKCYAN}[i] {message}{Colors.ENDC}")


def _python_has_dev(python_exe):
    if sys.platform != "win32":
        return True
    test_script = (
        "import sys, sysconfig, os;"
        "include_dir = sysconfig.get_path('include');"
        "base = sys.base_prefix or sys.prefix;"
        "ver = f'python{sys.version_info.major}{sys.version_info.minor}';"
        "lib_candidates = ["
        "os.path.join(base, 'libs', ver + '.lib'),"
        "os.path.join(base, 'libs', ver + '_d.lib')"
        "];"
        "ok = include_dir and os.path.exists(os.path.join(include_dir, 'Python.h')) and any(os.path.exists(p) for p in lib_candidates);"
        "sys.exit(0 if ok else 1)"
    )
    try:
        result = subprocess.run([python_exe, "-c", test_script], check=False)
        return result.returncode == 0
    except OSError:
        return False


def _list_py_launcher_executables():
    if sys.platform != "win32":
        return []
    try:
        result = subprocess.run(["py", "-0p"], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return []
    exes = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line.startswith("-"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            exes.append(parts[1])
    return exes


def _iter_python_candidates():
    candidates = [sys.executable]
    candidates.extend(_list_py_launcher_executables())
    seen = set()
    for exe in candidates:
        norm = exe.lower() if sys.platform == "win32" else exe
        if norm in seen:
            continue
        seen.add(norm)
        yield exe


def find_cmake():
    """Find CMake executable"""
    cmake_path = shutil.which("cmake")
    if cmake_path is None:
        print_error("CMake not found in PATH!")
        print_info("Please install CMake from https://cmake.org/download/")
        print_info("Make sure to add CMake to your system PATH during installation")
        sys.exit(1)
    
    # Get CMake version
    try:
        result = subprocess.run([cmake_path, "--version"], 
                              capture_output=True, text=True, check=True)
        version_line = result.stdout.split('\n')[0]
        print_success(f"Found {version_line}")
    except subprocess.CalledProcessError:
        print_error("Failed to get CMake version")
        sys.exit(1)
    
    return cmake_path


def find_msvc():
    """Find Visual Studio / MSVC compiler"""
    print_info("Looking for Visual Studio / MSVC compiler...")
    
    # Common Visual Studio installation paths
    vs_paths = [
        r"C:\Program Files\Microsoft Visual Studio\2022",
        r"C:\Program Files\Microsoft Visual Studio\2019",
        r"C:\Program Files (x86)\Microsoft Visual Studio\2022",
        r"C:\Program Files (x86)\Microsoft Visual Studio\2019",
    ]
    
    for vs_path in vs_paths:
        if os.path.exists(vs_path):
            print_success(f"Found Visual Studio installation at {vs_path}")
            return True
    
    print_error("Visual Studio not found!")
    print_info("This build system requires Microsoft Visual Studio with C++ support")
    print_info("Please install Visual Studio from https://visualstudio.microsoft.com/")
    print_info("Make sure to select 'Desktop development with C++' workload")
    
    # On non-Windows systems, this is expected
    if sys.platform != "win32":
        print_info("Note: On non-Windows systems, the system C++ compiler will be used")
        return True
    
    return False


def clean_build(build_dir, python_modules_dir):
    """Clean build artifacts"""
    print_header("Cleaning Build Artifacts")
    
    if build_dir.exists():
        print_info(f"Removing {build_dir}")
        shutil.rmtree(build_dir)
        print_success("Build directory cleaned")
    
    if python_modules_dir.exists():
        print_info(f"Removing {python_modules_dir}")
        shutil.rmtree(python_modules_dir)
        print_success("Python modules directory cleaned")
    
    print_success("Clean complete!")


def configure_cmake(cmake_path, source_dir, build_dir, build_type="Release", generator=None):
    """Configure CMake project"""
    print_header("Configuring CMake Project")
    
    # Create build directory
    build_dir.mkdir(parents=True, exist_ok=True)
    
    # CMake configure command
    base_cmd = [
        cmake_path,
        f"-S{source_dir}",
        f"-B{build_dir}",
        f"-DCMAKE_BUILD_TYPE={build_type}",
    ]
    python_candidates = list(_iter_python_candidates())
    python_candidates.append(None)

    def attempt_configure(use_generator):
        for python_exe in python_candidates:
            cmd = list(base_cmd)
            if python_exe:
                cmd.extend([
                    f"-DPython_EXECUTABLE={python_exe}",
                    f"-DPython3_EXECUTABLE={python_exe}",
                ])
                print_info(f"Using Python for CMake: {python_exe}")

            if use_generator:
                if generator:
                    cmd.extend(["-G", generator])
                elif sys.platform == "win32":
                    cmd.extend(["-G", "Visual Studio 17 2022"])

            print_info(f"Running: {' '.join(cmd)}")
            try:
                subprocess.run(cmd, check=True, cwd=source_dir)
                return True
            except subprocess.CalledProcessError:
                continue
        return False

    if attempt_configure(use_generator=True):
        print_success("CMake configuration successful!")
        return True

    if generator or sys.platform == "win32":
        print_info("Retrying without generator specification...")
        if attempt_configure(use_generator=False):
            print_success("CMake configuration successful (default generator)!")
            return True

    print_error("Unable to configure CMake project")
    return False


def build_project(cmake_path, build_dir, build_type="Release", target=None):
    """Build the CMake project"""
    print_header("Building C++ Project")
    
    # CMake build command
    cmd = [
        cmake_path,
        "--build", str(build_dir),
        "--config", build_type,
    ]
    
    if target:
        cmd.extend(["--target", target])
    
    # Add parallel build flag
    cmd.extend(["--parallel"])
    
    print_info(f"Running: {' '.join(cmd)}")
    
    try:
        subprocess.run(cmd, check=True)
        print_success("Build successful!")
        return True
    except subprocess.CalledProcessError as e:
        print_error(f"Build failed with exit code {e.returncode}")
        return False


def run_tests(build_dir, build_type="Release"):
    """Run standalone C++ tests"""
    print_header("Running Standalone C++ Tests")
    
    # Find test executable
    if sys.platform == "win32":
        test_exe = build_dir / build_type / "test_cpp.exe"
        if not test_exe.exists():
            test_exe = build_dir / "test_cpp.exe"
    else:
        test_exe = build_dir / "test_cpp"
    
    if not test_exe.exists():
        print_error(f"Test executable not found at {test_exe}")
        return False
    
    print_info(f"Running: {test_exe}")
    
    try:
        result = subprocess.run([str(test_exe)], check=True)
        print_success("All C++ tests passed!")
        return True
    except subprocess.CalledProcessError as e:
        print_error(f"Tests failed with exit code {e.returncode}")
        return False


def _read_cmake_python_executable(build_dir):
    cache_path = build_dir / "CMakeCache.txt"
    if not cache_path.exists():
        return None
    try:
        cache_text = cache_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    for line in cache_text.splitlines():
        if line.startswith("Python_EXECUTABLE:FILEPATH="):
            return line.split("=", 1)[1]
        if line.startswith("Python3_EXECUTABLE:FILEPATH="):
            return line.split("=", 1)[1]
    return None


def verify_python_module(python_modules_dir, build_dir, build_type=None):
    """Verify the Python module was built"""
    print_header("Verifying Python Module")
    
    # Look for the module
    module_patterns = [f"{MODULE_NAME}*.pyd", f"{MODULE_NAME}*.so", f"{MODULE_NAME}*.dll"]
    search_dirs = [python_modules_dir]
    if build_type:
        search_dirs.append(python_modules_dir / build_type)
    found_modules = []
    
    for search_dir in search_dirs:
        for pattern in module_patterns:
            found_modules.extend(search_dir.glob(pattern))
    
    if not found_modules:
        print_error("Python module not found!")
        print_info(f"Expected to find module in: {', '.join(str(d) for d in search_dirs)}")
        return False
    
    for module in found_modules:
        print_success(f"Found Python module: {module}")
    
    # Try to import the module
    print_info("Testing Python module import...")
    module_dir = found_modules[0].parent
    python_exe = _read_cmake_python_executable(build_dir)
    if python_exe:
        python_path = Path(python_exe)
    else:
        python_path = None

    if python_path and python_path.exists() and python_path.resolve() != Path(sys.executable).resolve():
        print_info(f"Testing Python module import with: {python_path}")
        test_script = (
            "import sys; "
            f"sys.path.insert(0, r'{module_dir}'); "
            f"import {MODULE_NAME} as m; "
            "print(f'Imported rc_car_cpp (version {m.__version__})'); "
            "mag = m.MathOperations.vector_magnitude(3.0, 4.0, 0.0); "
            "print(f'vector_magnitude(3, 4, 0) = {mag}'); "
            "assert abs(mag - 5.0) < 0.0001"
        )
        result = subprocess.run([str(python_path), "-c", test_script], capture_output=True, text=True)
        if result.returncode != 0:
            print_error(f"Failed to import module: {result.stderr.strip() or result.stdout.strip()}")
            return False
        for line in result.stdout.splitlines():
            print_info(line)
        print_success("Python module is working correctly!")
        return True

    sys.path.insert(0, str(module_dir))
    try:
        module = __import__(MODULE_NAME)
        print_success(f"Successfully imported {MODULE_NAME} module (version {module.__version__})")

        # Test basic functionality
        mag = module.MathOperations.vector_magnitude(3.0, 4.0, 0.0)
        print_info(f"Test: vector_magnitude(3, 4, 0) = {mag}")
        assert abs(mag - 5.0) < 0.0001, "Vector magnitude test failed!"
        print_success("Python module is working correctly!")

        return True
    except ImportError as e:
        print_error(f"Failed to import module: {e}")
        return False
    except Exception as e:
        print_error(f"Error testing module: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Build RC Car C++ extensions with CMake",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python setup_cpp.py build          # Build the C++ module
  python setup_cpp.py clean          # Clean build artifacts
  python setup_cpp.py rebuild        # Clean and rebuild
  python setup_cpp.py test           # Build and run C++ tests
  python setup_cpp.py build --debug  # Build in debug mode
        """
    )
    
    parser.add_argument(
        "command",
        choices=["build", "clean", "rebuild", "test"],
        help="Command to execute"
    )
    
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Build in debug mode (default: Release)"
    )
    
    parser.add_argument(
        "--generator",
        help="CMake generator to use (e.g., 'Visual Studio 17 2022')"
    )
    
    args = parser.parse_args()
    
    # Get directories
    root_dir = Path(__file__).parent.absolute()
    build_dir = root_dir / "build"
    python_modules_dir = root_dir / "python_modules"
    
    build_type = "Debug" if args.debug else "Release"
    
    print_header("RC Car C++ Build System")
    print_info(f"Root directory: {root_dir}")
    print_info(f"Build directory: {build_dir}")
    print_info(f"Build type: {build_type}")
    
    # Find required tools
    cmake_path = find_cmake()
    
    if sys.platform == "win32":
        find_msvc()
    
    # Execute command
    if args.command == "clean":
        clean_build(build_dir, python_modules_dir)
        return 0
    
    elif args.command == "build":
        if not configure_cmake(cmake_path, root_dir, build_dir, build_type, args.generator):
            return 1
        
        if not build_project(cmake_path, build_dir, build_type):
            return 1
        
        if not verify_python_module(python_modules_dir, build_dir, build_type):
            return 1
        
        print_header("Build Complete!")
        print_success("C++ module is ready to use")
        print_info(f"Import in Python with: import sys; sys.path.insert(0, '{python_modules_dir}'); import {MODULE_NAME}")
        return 0
    
    elif args.command == "rebuild":
        clean_build(build_dir, python_modules_dir)
        
        if not configure_cmake(cmake_path, root_dir, build_dir, build_type, args.generator):
            return 1
        
        if not build_project(cmake_path, build_dir, build_type):
            return 1
        
        if not verify_python_module(python_modules_dir):
            return 1
        
        print_header("Rebuild Complete!")
        return 0
    
    elif args.command == "test":
        if not configure_cmake(cmake_path, root_dir, build_dir, build_type, args.generator):
            return 1
        
        if not build_project(cmake_path, build_dir, build_type):
            return 1
        
        if not run_tests(build_dir, build_type):
            return 1
        
        print_header("All Tests Passed!")
        return 0


if __name__ == "__main__":
    sys.exit(main())
