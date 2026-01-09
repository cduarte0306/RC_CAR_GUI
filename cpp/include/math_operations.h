#pragma once

namespace rc_car {

/**
 * High-performance math operations for robotics calculations
 */
class MathOperations {
public:
    /**
     * Fast vector magnitude calculation
     */
    static double vectorMagnitude(double x, double y, double z);
    
    /**
     * Calculate angle between two vectors in radians
     */
    static double angleBetweenVectors(double x1, double y1, double z1, 
                                     double x2, double y2, double z2);
    
    /**
     * Normalize a 3D vector
     */
    static void normalizeVector(double& x, double& y, double& z);
};

} // namespace rc_car
