# RC_CAR_GUI
GUI for RC car telemetry and controls with high-performance C++ extensions and real-time 3D visualization.

## Features
- PyQt6-based graphical interface for RC car control
- High-performance C++ extensions via pybind11 for robotics calculations
- **GPU-accelerated 3D point cloud visualization** using Open3D + CUDA
- OpenGL-backed 3D visualization widget with Python bindings
- CMake-based build system with MSVC/Ninja support
- Standalone C++ testing capabilities

## Prerequisites

### Python Dependencies
- Python 3.10+ with development headers
- PyQt6
- opencv-python
- pydualsense
- zeroconf

### C++ Build Dependencies
- **CMake 3.15+**
- **Visual Studio 2022** with "Desktop development with C++" workload (Windows)
- GCC or Clang on Linux/macOS
- **Ninja** (recommended, auto-installed via pip)
- OpenGL development libraries

### Optional: GPU Acceleration
- **CUDA 12.x** (12.0-12.8) - Required for GPU-accelerated 3D processing
- ⚠️ **CUDA 13.x is NOT supported** (Thrust/cccl header incompatibility with Open3D's stdgpu)

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
pip install ninja  # Recommended for faster builds
```

### 3. Build C++ Extensions

#### CPU-only Build (simplest)
```bash
python setup_cpp.py build
```

#### GPU-accelerated Build (requires CUDA 12.x)
```bash
python setup_cpp.py build --cuda
```

#### Debug Build (debug symbols for your code, Open3D stays Release)
```bash
python setup_cpp.py build --debug --cuda
```

This will:
- Configure CMake with the appropriate compiler (Ninja preferred)
- Fetch and build Open3D with CUDA support (first build takes 30-60 minutes)
- Build the `rc_car_cpp` Python extension module
- Place the compiled module in `python_modules/`

### Build Commands Reference
| Command | Description |
|---------|-------------|
| `python setup_cpp.py build` | CPU-only Release build |
| `python setup_cpp.py build --cuda` | GPU-accelerated Release build |
| `python setup_cpp.py build --debug` | Debug build (CPU-only) |
| `python setup_cpp.py build --debug --cuda` | Debug build with CUDA |
| `python setup_cpp.py rebuild` | Clean and rebuild |
| `python setup_cpp.py rebuild --cuda` | Clean rebuild with CUDA |
| `python setup_cpp.py clean` | Remove all build artifacts |
| `python setup_cpp.py test` | Build and run C++ tests |

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
Install Visual Studio 2022 with "Desktop development with C++" workload from https://visualstudio.microsoft.com/

### CUDA build fails with "Thrust" or "cccl" errors
You have CUDA 13.x installed. Open3D requires CUDA 12.x due to Thrust header reorganization in CUDA 13.
- Install CUDA 12.x alongside CUDA 13.x
- The build script auto-detects and uses CUDA 12.x when available
- Or build without CUDA: `python setup_cpp.py build` (no `--cuda` flag)

### Build fails at CUDA compilation step with iterator errors
This happens with Debug builds + CUDA on MSVC. The `--debug` flag now automatically:
- Builds Open3D in Release mode (avoids MSVC iterator conflicts)
- Keeps debug symbols for your code (`rc_car_app`, `test_cpp`)

### Module import fails
- Ensure the build completed successfully
- Check that `python_modules/rc_car_cpp.pyd` (Windows) or `.so` (Linux) exists
- Verify Python version matches the one used during build

### First build is very slow
Normal! First build fetches and compiles Open3D + dependencies (~30-60 minutes).
Subsequent builds are incremental and much faster.

## License
[Add your license information here]
