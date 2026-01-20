#include "renderer3d.h"
#include <algorithm>

#ifdef _WIN32
#include <windows.h>
#include <GL/gl.h>
#elif defined(__APPLE__)
#include <OpenGL/gl.h>
#else
#include <GL/gl.h>
#endif

namespace rc_car {

namespace {
float clamp01(float v) {
    return std::max(0.0f, std::min(1.0f, v));
}
}  // namespace

Renderer3D::Renderer3D() : clearColor_{0.05f, 0.09f, 0.14f, 1.0f} {}

void Renderer3D::setClearColor(float r, float g, float b, float a) {
    clearColor_ = {
        clamp01(r),
        clamp01(g),
        clamp01(b),
        clamp01(a)
    };
}

void Renderer3D::renderCube(float angleXDeg, float angleYDeg, float distance, int width, int height) const {
    if (width <= 0 || height <= 0) {
        return;
    }

    glViewport(0, 0, width, height);
    glEnable(GL_DEPTH_TEST);

    glClearColor(clearColor_[0], clearColor_[1], clearColor_[2], clearColor_[3]);
    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);

    glMatrixMode(GL_PROJECTION);
    glLoadIdentity();

    const float aspect = static_cast<float>(width) / static_cast<float>(height);
    glFrustum(-aspect, aspect, -1.0, 1.0, 1.5, 20.0);

    glMatrixMode(GL_MODELVIEW);
    glLoadIdentity();
    glTranslatef(0.0f, 0.0f, -distance);
    glRotatef(angleXDeg, 1.0f, 0.0f, 0.0f);
    glRotatef(angleYDeg, 0.0f, 1.0f, 0.0f);

    glBegin(GL_QUADS);

    // Front face (red)
    glColor3f(0.84f, 0.27f, 0.27f);
    glVertex3f(-1.0f, -1.0f,  1.0f);
    glVertex3f( 1.0f, -1.0f,  1.0f);
    glVertex3f( 1.0f,  1.0f,  1.0f);
    glVertex3f(-1.0f,  1.0f,  1.0f);

    // Back face (cyan)
    glColor3f(0.16f, 0.67f, 0.84f);
    glVertex3f(-1.0f, -1.0f, -1.0f);
    glVertex3f(-1.0f,  1.0f, -1.0f);
    glVertex3f( 1.0f,  1.0f, -1.0f);
    glVertex3f( 1.0f, -1.0f, -1.0f);

    // Left face (green)
    glColor3f(0.23f, 0.82f, 0.39f);
    glVertex3f(-1.0f, -1.0f, -1.0f);
    glVertex3f(-1.0f, -1.0f,  1.0f);
    glVertex3f(-1.0f,  1.0f,  1.0f);
    glVertex3f(-1.0f,  1.0f, -1.0f);

    // Right face (yellow)
    glColor3f(0.96f, 0.82f, 0.26f);
    glVertex3f(1.0f, -1.0f, -1.0f);
    glVertex3f(1.0f,  1.0f, -1.0f);
    glVertex3f(1.0f,  1.0f,  1.0f);
    glVertex3f(1.0f, -1.0f,  1.0f);

    // Top face (purple)
    glColor3f(0.67f, 0.34f, 0.90f);
    glVertex3f(-1.0f, 1.0f,  1.0f);
    glVertex3f( 1.0f, 1.0f,  1.0f);
    glVertex3f( 1.0f, 1.0f, -1.0f);
    glVertex3f(-1.0f, 1.0f, -1.0f);

    // Bottom face (blue)
    glColor3f(0.11f, 0.56f, 0.83f);
    glVertex3f(-1.0f, -1.0f,  1.0f);
    glVertex3f(-1.0f, -1.0f, -1.0f);
    glVertex3f( 1.0f, -1.0f, -1.0f);
    glVertex3f( 1.0f, -1.0f,  1.0f);

    glEnd();
}

}  // namespace rc_car
