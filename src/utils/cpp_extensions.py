"""
Integration helper for C++ module in the RC Car application.

This module handles loading the C++ extension and provides a fallback
to pure Python implementations if the C++ module is not available.
"""

import sys
import logging
from pathlib import Path

# Try to import the C++ module
_cpp_module_available = False
_rc_car_cpp = None
try:
    # Add python_modules to path if not already there
    # Navigate up from src/utils/ to root
    root_dir = Path(__file__).parent.parent.parent
    python_modules_dir = root_dir / "python_modules"
    
    if str(python_modules_dir) not in sys.path:
        sys.path.insert(0, str(python_modules_dir))
    
    import rc_car_cpp as _rc_car_cpp
    _cpp_module_available = True
    logging.info(f"C++ module loaded successfully (version {_rc_car_cpp.__version__})")
except ImportError as e:
    logging.warning(f"C++ module not available, using pure Python implementations: {e}")
    logging.info("To enable C++ acceleration, run: python setup_cpp.py build")


def is_cpp_available():
    """Check if the C++ module is available"""
    return _cpp_module_available


def vector_magnitude(x, y, z):
    """
    Calculate the magnitude of a 3D vector.
    Uses C++ implementation if available, otherwise falls back to Python.
    
    Args:
        x, y, z: Vector components
        
    Returns:
        float: Magnitude of the vector
    """
    if _cpp_module_available:
        return _rc_car_cpp.MathOperations.vector_magnitude(x, y, z)
    else:
        # Pure Python fallback
        return (x**2 + y**2 + z**2) ** 0.5


def angle_between_vectors(x1, y1, z1, x2, y2, z2):
    """
    Calculate the angle between two 3D vectors in radians.
    Uses C++ implementation if available, otherwise falls back to Python.
    
    Args:
        x1, y1, z1: First vector components
        x2, y2, z2: Second vector components
        
    Returns:
        float: Angle in radians
    """
    if _cpp_module_available:
        return _rc_car_cpp.MathOperations.angle_between_vectors(x1, y1, z1, x2, y2, z2)
    else:
        # Pure Python fallback
        import math
        
        dot = x1 * x2 + y1 * y2 + z1 * z2
        mag1 = vector_magnitude(x1, y1, z1)
        mag2 = vector_magnitude(x2, y2, z2)
        
        if mag1 == 0.0 or mag2 == 0.0:
            return 0.0
        
        cos_angle = dot / (mag1 * mag2)
        # Clamp to [-1, 1] to avoid numerical errors
        cos_angle = max(-1.0, min(1.0, cos_angle))
        return math.acos(cos_angle)


def normalize_vector(x, y, z):
    """
    Normalize a 3D vector.
    Uses C++ implementation if available, otherwise falls back to Python.
    
    Args:
        x, y, z: Vector components
        
    Returns:
        tuple: Normalized vector (x, y, z)
    """
    if _cpp_module_available:
        return _rc_car_cpp.MathOperations.normalize_vector(x, y, z)
    else:
        # Pure Python fallback
        mag = vector_magnitude(x, y, z)
        if mag > 0.0:
            return (x / mag, y / mag, z / mag)
        else:
            return (x, y, z)


# Example usage
if __name__ == "__main__":
    print("C++ Extensions Integration")
    print(f"C++ module available: {is_cpp_available()}")
    print()
    
    # Test functions
    print("Testing vector operations:")
    mag = vector_magnitude(3.0, 4.0, 0.0)
    print(f"  vector_magnitude(3, 4, 0) = {mag}")
    
    angle = angle_between_vectors(1, 0, 0, 0, 1, 0)
    print(f"  angle_between_vectors((1,0,0), (0,1,0)) = {angle:.4f} radians")
    
    norm = normalize_vector(3.0, 4.0, 0.0)
    print(f"  normalize_vector(3, 4, 0) = {norm}")
