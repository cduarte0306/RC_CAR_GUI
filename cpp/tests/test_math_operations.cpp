#include "math_operations.h"
#include <iostream>
#include <iomanip>
#include <cassert>
#include <cmath>

using namespace rc_car;

constexpr double kPi = 3.14159265358979323846;

void testVectorMagnitude() {
    std::cout << "Testing vector magnitude..." << std::endl;
    
    double mag1 = MathOperations::vectorMagnitude(3.0, 4.0, 0.0);
    std::cout << "  Magnitude of (3, 4, 0): " << mag1 << " (expected 5.0)" << std::endl;
    assert(std::abs(mag1 - 5.0) < 0.0001);
    
    double mag2 = MathOperations::vectorMagnitude(1.0, 1.0, 1.0);
    std::cout << "  Magnitude of (1, 1, 1): " << mag2 << " (expected ~1.732)" << std::endl;
    assert(std::abs(mag2 - std::sqrt(3.0)) < 0.0001);
    
    std::cout << "  ✓ Vector magnitude tests passed!" << std::endl;
}

void testAngleBetweenVectors() {
    std::cout << "Testing angle between vectors..." << std::endl;
    
    // Perpendicular vectors (90 degrees = pi/2 radians)
    double angle1 = MathOperations::angleBetweenVectors(1.0, 0.0, 0.0, 0.0, 1.0, 0.0);
    std::cout << "  Angle between (1,0,0) and (0,1,0): " << angle1 
              << " radians (expected " << kPi / 2.0 << ")" << std::endl;
    assert(std::abs(angle1 - kPi / 2.0) < 0.0001);
    
    // Parallel vectors (0 degrees)
    double angle2 = MathOperations::angleBetweenVectors(1.0, 0.0, 0.0, 2.0, 0.0, 0.0);
    std::cout << "  Angle between (1,0,0) and (2,0,0): " << angle2 
              << " radians (expected 0)" << std::endl;
    assert(std::abs(angle2) < 0.0001);
    
    // Opposite vectors (180 degrees = pi radians)
    double angle3 = MathOperations::angleBetweenVectors(1.0, 0.0, 0.0, -1.0, 0.0, 0.0);
    std::cout << "  Angle between (1,0,0) and (-1,0,0): " << angle3 
              << " radians (expected " << kPi << ")" << std::endl;
    assert(std::abs(angle3 - kPi) < 0.0001);
    
    std::cout << "  ✓ Angle between vectors tests passed!" << std::endl;
}

void testNormalizeVector() {
    std::cout << "Testing vector normalization..." << std::endl;
    
    double x = 3.0, y = 4.0, z = 0.0;
    MathOperations::normalizeVector(x, y, z);
    std::cout << "  Normalized (3, 4, 0): (" << x << ", " << y << ", " << z << ")" << std::endl;
    std::cout << "  Expected: (0.6, 0.8, 0)" << std::endl;
    assert(std::abs(x - 0.6) < 0.0001);
    assert(std::abs(y - 0.8) < 0.0001);
    assert(std::abs(z) < 0.0001);
    
    double mag = MathOperations::vectorMagnitude(x, y, z);
    std::cout << "  Magnitude after normalization: " << mag << " (expected 1.0)" << std::endl;
    assert(std::abs(mag - 1.0) < 0.0001);
    
    std::cout << "  ✓ Vector normalization tests passed!" << std::endl;
}

int main() {
    std::cout << std::fixed << std::setprecision(6);
    std::cout << "=== RC Car C++ Module Standalone Tests ===" << std::endl << std::endl;
    
    try {
        testVectorMagnitude();
        std::cout << std::endl;
        
        testAngleBetweenVectors();
        std::cout << std::endl;
        
        testNormalizeVector();
        std::cout << std::endl;
        
        std::cout << "=== All tests passed! ===" << std::endl;
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "Error: " << e.what() << std::endl;
        return 1;
    }
}
