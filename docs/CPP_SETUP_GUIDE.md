# C++ Extensions Setup Guide

## Overview

This project integrates high-performance C++ extensions into the Python application using Pybind11 and CMake. This guide explains how to set up, build, and use the C++ extensions.

## Prerequisites

### Required Tools

1. **Python 3.8+**: The main application runtime
2. **CMake 3.15+**: Build system generator
3. **C++ Compiler**:
   - **Windows**: Microsoft Visual Studio 2019+ with "Desktop development with C++" workload
   - **Linux**: GCC or Clang (typically pre-installed)
   - **macOS**: Xcode Command Line Tools

### Installing Prerequisites

#### Windows

1. Install CMake from https://cmake.org/download/
   - During installation, select "Add CMake to the system PATH"

2. Install Visual Studio from https://visualstudio.microsoft.com/
   - Select "Desktop development with C++" workload
   - Community edition is free and sufficient

#### Linux (Ubuntu/Debian)

```bash
sudo apt update
sudo apt install cmake build-essential python3-dev
```

#### macOS

```bash
# Install Xcode Command Line Tools
xcode-select --install

# Install CMake via Homebrew
brew install cmake
```

## Quick Start

### 1. Clone and Initialize Submodules

```bash
git clone <repository-url>
cd RC_CAR_GUI
git submodule update --init --recursive
```

### 2. Install Python Dependencies

```bash
pip install -r requirements.txt
```

### 3. Build C++ Extensions

```bash
python setup_cpp.py build
```

This will:
- Configure the CMake project
- Compile the C++ code
- Generate a Python-importable module in `python_modules/`
- Verify the module can be imported

### 4. Run the Application

```bash
cd src
python run.py
```

## Build Commands

The `setup_cpp.py` script provides several commands:

### Build (Release Mode)
```bash
python setup_cpp.py build
```
Compiles the C++ extension in release mode with optimizations enabled.

### Build (Debug Mode)
```bash
python setup_cpp.py build --debug
```
Compiles with debug symbols and without optimizations. Useful for debugging C++ code.

### Rebuild
```bash
python setup_cpp.py rebuild
```
Cleans all build artifacts and performs a fresh build.

### Clean
```bash
python setup_cpp.py clean
```
Removes all build artifacts and compiled modules.

### Test
```bash
python setup_cpp.py test
```
Builds the project and runs standalone C++ tests.

## VSCode Integration

### Build Tasks

The project includes VSCode tasks for easy C++ development. Access them via:
- `Ctrl+Shift+B` (default build task)
- `Ctrl+Shift+P` → "Tasks: Run Task"

Available tasks:
1. **Build C++ Module** - Build in release mode (default)
2. **Build C++ Module (Debug)** - Build in debug mode
3. **Rebuild C++ Module** - Clean and rebuild
4. **Clean C++ Build** - Remove build artifacts
5. **Test C++ Module** - Run standalone C++ tests

### Running Tasks

1. Press `Ctrl+Shift+B` to build the C++ module
2. Or press `F1`, type "Tasks: Run Task", and select a task
3. Build output appears in the integrated terminal

## Project Structure

```
RC_CAR_GUI/
├── cpp/                           # C++ source code
│   ├── include/                   # Public header files
│   │   └── math_operations.h
│   ├── src/                       # Implementation files
│   │   ├── math_operations.cpp
│   │   └── bindings.cpp           # Pybind11 bindings
│   └── tests/                     # Standalone C++ tests
│       └── test_math_operations.cpp
├── src/                           # Python application
│   └── utils/
│       └── cpp_extensions.py      # Python integration helper
├── external/
│   └── pybind11/                  # Pybind11 submodule
├── build/                         # Build artifacts (generated, gitignored)
├── python_modules/                # Compiled Python modules (generated, gitignored)
├── CMakeLists.txt                 # CMake configuration
├── setup_cpp.py                   # Build automation script
└── example_cpp_usage.py           # Usage examples
```

## Using C++ Extensions in Python

### Direct Import

```python
import sys
sys.path.insert(0, 'python_modules')
import rc_car_cpp

# Use C++ functions
magnitude = rc_car_cpp.MathOperations.vector_magnitude(3.0, 4.0, 0.0)
angle = rc_car_cpp.MathOperations.angle_between_vectors(1, 0, 0, 0, 1, 0)
x, y, z = rc_car_cpp.MathOperations.normalize_vector(3.0, 4.0, 0.0)
```

### Using the Integration Helper (Recommended)

The integration helper provides automatic fallback to pure Python if C++ module is not built:

```python
from utils.cpp_extensions import vector_magnitude, angle_between_vectors, normalize_vector

# These functions automatically use C++ if available, otherwise pure Python
magnitude = vector_magnitude(3.0, 4.0, 0.0)
angle = angle_between_vectors(1, 0, 0, 0, 1, 0)
x, y, z = normalize_vector(3.0, 4.0, 0.0)
```

Check if C++ is available:
```python
from utils.cpp_extensions import is_cpp_available

if is_cpp_available():
    print("Using C++ accelerated functions")
else:
    print("Using pure Python implementations")
```

## Troubleshooting

### "CMake not found"
- Verify CMake is installed: `cmake --version`
- On Windows, ensure CMake is in system PATH
- Restart terminal/VSCode after installation

### "Visual Studio not found" (Windows)
- Install Visual Studio with C++ workload
- Or use Build Tools for Visual Studio (lighter installation)
- Community edition is free

### "Module import fails"
- Ensure build completed successfully: `python setup_cpp.py build`
- Check `python_modules/` directory contains `.pyd` (Windows) or `.so` (Linux) file
- Verify Python version matches (check with `python --version`)

### Build errors
- Clean and rebuild: `python setup_cpp.py clean && python setup_cpp.py build`
- Check C++ compiler version meets minimum requirements (C++17 support)
- Ensure submodules are initialized: `git submodule update --init --recursive`

### VSCode tasks not working
- Ensure Python extension is installed in VSCode
- Select correct Python interpreter: `Ctrl+Shift+P` → "Python: Select Interpreter"
- Check `.vscode/tasks.json` exists

## Best Practices

1. **Always test standalone**: Verify C++ code works independently before integrating
2. **Use the integration helper**: Provides graceful fallback to Python
3. **Write tests**: Add C++ tests in `cpp/tests/` directory
4. **Document bindings**: Add docstrings in Pybind11 bindings
5. **Keep it simple**: Only move performance-critical code to C++
6. **Rebuild after changes**: Use VSCode task or `python setup_cpp.py rebuild`
7. **Version control**: Never commit `build/` or `python_modules/` directories

## Additional Resources

- [Pybind11 Documentation](https://pybind11.readthedocs.io/)
- [CMake Documentation](https://cmake.org/documentation/)
- [C++17 Reference](https://en.cppreference.com/)
