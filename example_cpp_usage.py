#!/usr/bin/env python3
"""
Example script demonstrating the use of C++ extensions in Python.

This shows how to import and use the high-performance C++ module
from the Python application.
"""

import sys
from pathlib import Path

# Add python_modules to path
root_dir = Path(__file__).parent
python_modules_dir = root_dir / "python_modules"
sys.path.insert(0, str(python_modules_dir))

try:
    import rc_car_cpp
    
    print("=" * 60)
    print("RC Car C++ Module - Python Integration Example")
    print("=" * 60)
    print()
    
    print(f"Module version: {rc_car_cpp.__version__}")
    print()
    
    # Example 1: Vector magnitude
    print("Example 1: Vector Magnitude")
    print("-" * 40)
    x, y, z = 3.0, 4.0, 0.0
    magnitude = rc_car_cpp.MathOperations.vector_magnitude(x, y, z)
    print(f"Vector: ({x}, {y}, {z})")
    print(f"Magnitude: {magnitude}")
    print(f"Expected: 5.0")
    print()
    
    # Example 2: Angle between vectors
    print("Example 2: Angle Between Vectors")
    print("-" * 40)
    v1 = (1.0, 0.0, 0.0)
    v2 = (0.0, 1.0, 0.0)
    angle_rad = rc_car_cpp.MathOperations.angle_between_vectors(*v1, *v2)
    import math
    angle_deg = math.degrees(angle_rad)
    print(f"Vector 1: {v1}")
    print(f"Vector 2: {v2}")
    print(f"Angle: {angle_rad:.4f} radians ({angle_deg:.1f} degrees)")
    print(f"Expected: ~1.5708 radians (90.0 degrees)")
    print()
    
    # Example 3: Vector normalization
    print("Example 3: Vector Normalization")
    print("-" * 40)
    x, y, z = 10.0, 20.0, 5.0
    print(f"Original vector: ({x}, {y}, {z})")
    normalized = rc_car_cpp.MathOperations.normalize_vector(x, y, z)
    print(f"Normalized vector: ({normalized[0]:.4f}, {normalized[1]:.4f}, {normalized[2]:.4f})")
    
    # Verify it's normalized (magnitude should be 1.0)
    mag = rc_car_cpp.MathOperations.vector_magnitude(*normalized)
    print(f"Magnitude of normalized vector: {mag:.4f}")
    print(f"Expected: 1.0000")
    print()
    
    # Example 4: Performance comparison
    print("Example 4: Performance Comparison")
    print("-" * 40)
    import time
    
    # Pure Python implementation
    def python_vector_magnitude(x, y, z):
        return (x**2 + y**2 + z**2) ** 0.5
    
    # Test data
    test_count = 100000
    test_vectors = [(float(i), float(i+1), float(i+2)) for i in range(test_count)]
    
    # Benchmark Python implementation
    start = time.perf_counter()
    for vec in test_vectors:
        _ = python_vector_magnitude(*vec)
    python_time = time.perf_counter() - start
    
    # Benchmark C++ implementation
    start = time.perf_counter()
    for vec in test_vectors:
        _ = rc_car_cpp.MathOperations.vector_magnitude(*vec)
    cpp_time = time.perf_counter() - start
    
    print(f"Iterations: {test_count:,}")
    print(f"Pure Python time: {python_time:.4f} seconds")
    print(f"C++ extension time: {cpp_time:.4f} seconds")
    print(f"Speedup: {python_time/cpp_time:.2f}x faster")
    print()
    
    print("=" * 60)
    print("✓ All examples completed successfully!")
    print("=" * 60)
    
except ImportError as e:
    print("Error: Could not import rc_car_cpp module")
    print(f"Details: {e}")
    print()
    print("Please build the C++ module first:")
    print("  python setup_cpp.py build")
    sys.exit(1)
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
