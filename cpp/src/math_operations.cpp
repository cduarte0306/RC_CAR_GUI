#include "math_operations.h"
#include <algorithm>
#include <cmath>

namespace rc_car {

double MathOperations::vectorMagnitude(double x, double y, double z) {
    return std::sqrt(x * x + y * y + z * z);
}

double MathOperations::angleBetweenVectors(double x1, double y1, double z1,
                                          double x2, double y2, double z2) {
    double dot = x1 * x2 + y1 * y2 + z1 * z2;
    double mag1 = vectorMagnitude(x1, y1, z1);
    double mag2 = vectorMagnitude(x2, y2, z2);
    
    if (mag1 == 0.0 || mag2 == 0.0) {
        return 0.0;
    }
    
    double cosAngle = dot / (mag1 * mag2);
    // Clamp to [-1, 1] to avoid numerical errors
    cosAngle = std::max(-1.0, std::min(1.0, cosAngle));
    return std::acos(cosAngle);
}

void MathOperations::normalizeVector(double& x, double& y, double& z) {
    double mag = vectorMagnitude(x, y, z);
    if (mag > 0.0) {
        x /= mag;
        y /= mag;
        z /= mag;
    }
}

} // namespace rc_car
