# RC_CAR_GUI
GUI for RC car telemetry and controls with high-performance C++ extensions.

## Features
- PyQt6-based graphical interface for RC car control
- High-performance C++ extensions via Pybind11 for robotics calculations
- OpenGL-backed 3D visualization widget with Python bindings
- CMake-based build system with MSVC support
- Standalone C++ testing capabilities

## Prerequisites

### Python Dependencies
- Python 3.8 or higher
- PyQt6
- opencv-python
- pydualsense
- zeroconf

### C++ Build Dependencies
- CMake 3.15 or higher
- Microsoft Visual Studio (with C++ Desktop Development workload) on Windows
- GCC or Clang on Linux/macOS
- OpenGL development libraries (e.g., `libgl1-mesa-dev` and `libglu1-mesa-dev` on Linux)

## Installation

### 1. Clone the Repository
```bash
git clone https://github.com/cduarte0306/RC_CAR_GUI.git
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
- Configure CMake with the appropriate compiler
- Build the C++ extension module as a Python-importable DLL
- Place the compiled module in the `python_modules/` directory

#### Build Commands
- `python setup_cpp.py build` - Build the C++ module (Release mode)
- `python setup_cpp.py build --debug` - Build in debug mode
- `python setup_cpp.py rebuild` - Clean and rebuild
- `python setup_cpp.py clean` - Remove all build artifacts
- `python setup_cpp.py test` - Build and run standalone C++ tests

## Running the Application

From the project root:
```bash
cd src
python run.py
```

## C++ Module Development

### Project Structure
```
RC_CAR_GUI/
├── cpp/                      # C++ source code
│   ├── include/              # Header files
│   │   └── math_operations.h
│   ├── src/                  # Implementation files
│   │   ├── math_operations.cpp
│   │   └── bindings.cpp      # Pybind11 bindings
│   └── tests/                # Standalone C++ tests
│       └── test_math_operations.cpp
├── external/
│   └── pybind11/             # Pybind11 submodule
├── python_modules/           # Compiled Python modules (generated)
├── src/                      # Python application source
├── CMakeLists.txt            # CMake configuration
└── setup_cpp.py              # Build automation script
```

### VSCode Tasks
The project includes VSCode tasks for easy C++ development:
- **Build C++ Module** (Ctrl+Shift+B) - Build in release mode
- **Build C++ Module (Debug)** - Build in debug mode
- **Rebuild C++ Module** - Clean and rebuild
- **Clean C++ Build** - Remove build artifacts
- **Test C++ Module** - Run standalone C++ tests

Access tasks via: Terminal > Run Task... or press `Ctrl+Shift+P` and type "Tasks: Run Task"

### Using C++ Extensions in Python
```python
import sys
sys.path.insert(0, 'python_modules')
import rc_car_cpp

# Use high-performance C++ functions
magnitude = rc_car_cpp.MathOperations.vector_magnitude(3.0, 4.0, 0.0)
angle = rc_car_cpp.MathOperations.angle_between_vectors(1, 0, 0, 0, 1, 0)
x, y, z = rc_car_cpp.MathOperations.normalize_vector(3.0, 4.0, 0.0)
```

### Standalone C++ Testing
The C++ code can be built and tested independently:
```bash
python setup_cpp.py test
```

Or run the executable directly after building:
```bash
# Windows
build\Release\test_cpp.exe

# Linux/macOS
build/test_cpp
```

## Development Workflow

1. Make changes to C++ code in `cpp/` directory
2. Rebuild the module: `python setup_cpp.py rebuild` or use VSCode task (Ctrl+Shift+B)
3. Test standalone: `python setup_cpp.py test`
4. Use in Python application

## Troubleshooting

### CMake not found
Install CMake from https://cmake.org/download/ and add it to your PATH.

### Visual Studio not found (Windows)
Install Visual Studio with "Desktop development with C++" workload from https://visualstudio.microsoft.com/

### Module import fails
Ensure the module was built successfully and the `python_modules/` directory contains the compiled module (.pyd on Windows, .so on Linux).

## License
[Add your license information here]
