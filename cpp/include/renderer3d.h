#pragma once

#include <array>

namespace rc_car {

/**
 * Minimal OpenGL-based renderer for showcasing 3D content.
 *
 * Rendering calls assume an active OpenGL context (provided by the caller).
 */
class Renderer3D {
public:
    Renderer3D();

    /**
     * Set the clear color used before drawing.
     */
    void setClearColor(float r, float g, float b, float a = 1.0f);

    /**
     * Render a colored cube using the current OpenGL context.
     *
     * @param angleXDeg Rotation around the X axis in degrees.
     * @param angleYDeg Rotation around the Y axis in degrees.
     * @param distance  Camera distance from the object.
     * @param width     Viewport width in pixels.
     * @param height    Viewport height in pixels.
     */
    void renderCube(float angleXDeg, float angleYDeg, float distance, int width, int height) const;

private:
    std::array<float, 4> clearColor_;
};

}  // namespace rc_car
