# RC Car GUI - AI Coding Instructions

## Architecture Overview

This is a **hybrid Python/C++ application** for RC car telemetry and control with real-time 3D visualization.

**Layer structure:**
- **Python UI Layer** ([src/ui/](src/ui/)) - PyQt6 GUI with `MainWindow.py` as the entry point; uses signal-based navigation via `SidePanel`
- **Python Backend** ([src/ui/UIConsumer.py](src/ui/UIConsumer.py)) - `BackendIface` (QThread) bridges UI ↔ network/hardware; emits Qt signals for async data
- **C++ Extensions** ([cpp/](cpp/)) - High-performance module (`rc_car_cpp`) built via pybind11 for math operations and OpenGL rendering
- **Network Layer** ([src/network/](src/network/)) - UDP-based communication with `NetworkManager` managing socket pools and host discovery

## Build & Run Commands

```bash
# Build C++ module (required before running)
python setup_cpp.py build           # CPU-only Release build
python setup_cpp.py build --cuda    # CUDA-enabled Release build (requires CUDA 12.x)
python setup_cpp.py build --debug   # Debug build (CPU-only)
python setup_cpp.py build --debug --cuda  # Debug build with CUDA
python setup_cpp.py rebuild         # Clean + build
python setup_cpp.py test            # Run C++ tests

# Run the application (from project root)
cd src && python run.py
```

**Build Requirements:**
- CMake 3.15+ and Ninja (auto-detected from pip: `pip install ninja`)
- Visual Studio 2022 with C++ Desktop workload (Windows)
- **CUDA 12.x** for GPU builds - CUDA 13.x is NOT compatible (Thrust/cccl header reorganization breaks Open3D's stdgpu)
- Python with development headers (Anaconda recommended)

**VSCode Tasks** available: `Ctrl+Shift+B` for default build, or `Terminal > Run Task` for all options.

## Key Patterns

### Signal System
Custom `Signal` class in [src/utils/utilities.py](src/utils/utilities.py) provides Qt-like signals for non-Qt classes:
```python
from utils.utilities import Signal
hostDiscovered = Signal()
hostDiscovered.connect(callback_fn)
hostDiscovered.emit(data)
```

### C++ Module Integration
Always use the wrapper in [src/utils/cpp_extensions.py](src/utils/cpp_extensions.py) - provides Python fallbacks when C++ unavailable:
```python
from utils import cpp_extensions
cpp_extensions.vector_magnitude(x, y, z)  # Auto-selects C++/Python
renderer = cpp_extensions.create_renderer3d(clear_color)
```

### Adding New C++ Functions
1. Declare in `cpp/include/*.h`, implement in `cpp/src/*.cpp`
2. Add pybind11 bindings in [cpp/src/bindings.cpp](cpp/src/bindings.cpp)
3. Expose via [src/utils/cpp_extensions.py](src/utils/cpp_extensions.py) with Python fallback

### Network Communication
UDP adapters use callback-driven reception:
```python
adapter = network_manager.openAdapter("name", (port, ip), recv_callback)
```
Leave `ip` empty for receive-only binding. Data flows through `CircularBuffer` to UI via Qt signals.

## Project Conventions

- **Config**: INI format in [src/config/rc-car-viewer-config.ini](src/config/rc-car-viewer-config.ini)
- **Logging**: Configured in `run.py`; dual-writes to `src/logs/app.log` + timestamped files
- **UI Theme**: Dark gradient theme via [src/ui/theme.py](src/ui/theme.py); use `make_card()` for consistent card styling
- **Threading**: Use daemon threads with `Event` for shutdown coordination (see `Controller` pattern)
- **C++ Namespace**: All C++ code under `rc_car` namespace

## Dependencies

- **Python**: PyQt6, opencv-python, pydualsense, zeroconf
- **C++**: CMake 3.15+, MSVC (Windows), pybind11 (submodule), OpenGL, Open3D (fetched by CMake)
- **Build Output**: Compiled `.pyd`/`.so` goes to `python_modules/` directory
