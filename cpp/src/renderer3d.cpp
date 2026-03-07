#include "renderer3d.h"
#include <algorithm>
#include <limits>
#include <cmath>
#include <chrono>

// Prevent windows.h from defining min/max macros that conflict with std::min/max
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#ifdef RC_CAR_HAS_OPEN3D
#include <Eigen/Dense>
#include <open3d/Open3D.h>
#endif

namespace rc_car {

// Full implementation with Open3D support (for standalone app)

Renderer3D::Renderer3D() 
    : pcd(std::make_shared<open3d::geometry::PointCloud>()),
      mesh(std::make_shared<open3d::geometry::TriangleMesh>()) {
    // Don't create window automatically - prevents OpenGL context conflicts with PyQt6
    // Call enableVisualizerWindow(true) if you need visualization
    // Match the PyQt dark theme (#0b111c)
    clearColor_ = {0.043f, 0.067f, 0.110f, 1.0f};
}

Renderer3D::~Renderer3D() {
    enableVisualizerWindow(false);
}

void Renderer3D::setClearColor(float r, float g, float b, float a) {
    std::lock_guard<std::mutex> lock(dataMutex_);
    clearColor_[0] = r;
    clearColor_[1] = g;
    clearColor_[2] = b;
    clearColor_[3] = a;
}

void Renderer3D::enableVisualizerWindow(bool enable) {
    if (enable) {
        if (visualizerEnabled_.load()) {
            return;
        }
        stopRequested_.store(false);
        viewInitialized_.store(false);
        visualizerEnabled_.store(true);
        m_RenderThread_ = std::thread(&Renderer3D::renderLoop_, this);
        return;
    }

    if (!visualizerEnabled_.load()) {
        return;
    }
    stopRequested_.store(true);
    dataCv_.notify_all();
    if (m_RenderThread_.joinable()) {
        m_RenderThread_.join();
    }

    visualizerEnabled_.store(false);
}

std::uintptr_t Renderer3D::GetWindowId() const {
#ifdef _WIN32
    const HWND hwnd = FindWindowA(nullptr, "Point Cloud");
    return reinterpret_cast<std::uintptr_t>(hwnd);
#else
    return 0;
#endif
}

void Renderer3D::setPointCloudData(char* pcData, size_t numPoints) {
    if (pcData == nullptr) {
        return;
    }

    constexpr float kMinDepthMeters = 0.15f;
    constexpr float kMaxDepthMeters = 45.0f;
    constexpr float kMaxAbsXMeters = 12.0f;
    constexpr float kMaxAbsYMeters = 8.0f;

    const auto* points = reinterpret_cast<const PointXYZ*>(pcData);
    std::vector<PointXYZ> pointsBuffer;
    pointsBuffer.reserve(numPoints);
    for (size_t i = 0; i < numPoints; i++) {
        const PointXYZ& p = points[i];
        if (!std::isfinite(p.x) || !std::isfinite(p.y) || !std::isfinite(p.z)) {
            continue;
        }
        if (p.z < kMinDepthMeters || p.z > kMaxDepthMeters) {
            continue;
        }
        if (std::abs(p.x) > kMaxAbsXMeters || std::abs(p.y) > kMaxAbsYMeters) {
            continue;
        }
        pointsBuffer.push_back(p);
    }

    {
        std::lock_guard<std::mutex> lock(dataMutex_);
        latestPoints_ = std::move(pointsBuffer);
        hasNewPoints_ = true;
    }
    dataCv_.notify_one();
}

void Renderer3D::setPointCloudColorData(char* pcData, char* rgbData, size_t numPoints) {
    if (pcData == nullptr || rgbData == nullptr) {
        return;
    }

    constexpr float kMinDepthMeters = 0.15f;
    constexpr float kMaxDepthMeters = 45.0f;
    constexpr float kMaxAbsXMeters = 12.0f;
    constexpr float kMaxAbsYMeters = 8.0f;

    const auto* points = reinterpret_cast<const PointXYZ*>(pcData);
    const auto* color = reinterpret_cast<const PointRGB*>(rgbData);
    std::vector<PointXYZ> pointsBuffer;
    std::vector<PointRGB> colorsBuffer;
    pointsBuffer.reserve(numPoints);
    colorsBuffer.reserve(numPoints);
    for (size_t i = 0; i < numPoints; i++) {
        const PointXYZ& p = points[i];
        const PointRGB& colors = color[i];
        if (!std::isfinite(p.x) || !std::isfinite(p.y) || !std::isfinite(p.z)) {
            continue;
        }
        if (p.z < kMinDepthMeters || p.z > kMaxDepthMeters) {
            continue;
        }
        if (std::abs(p.x) > kMaxAbsXMeters || std::abs(p.y) > kMaxAbsYMeters) {
            continue;
        }
        pointsBuffer.push_back(p);
        colorsBuffer.push_back(colors);
    }

    {
        std::lock_guard<std::mutex> lock(dataMutex_);
        latestPoints_ = std::move(pointsBuffer);
        latestColorsPoints_ = std::move(colorsBuffer);
        m_HasNewColorPoints = true;
    }
    dataCv_.notify_one();
}

void Renderer3D::renderLoop_() {
    try {
        if (!vis.CreateVisualizerWindow("Point Cloud", 1280, 720, 80, 30)) {
            visualizerEnabled_.store(false);
            return;
        }

        int nb_neighbors = 20;
        double std_ratio = 2.0;

        // Seed with a dummy point so Open3D doesn't spam warnings on empty AABB.
        pcd->Clear();
        pcd->points_.push_back(Eigen::Vector3d{0.0, 0.0, 0.0});
        pcd->colors_.push_back(Eigen::Vector3d{0.0, 0.0, 0.0});

        vis.AddGeometry(pcd);
        vis.AddGeometry(mesh);
        vis.GetRenderOption().point_size_ = 3.0;
        {
            std::lock_guard<std::mutex> lock(dataMutex_);
            vis.GetRenderOption().background_color_ = Eigen::Vector3d{
                static_cast<double>(clearColor_[0]),
                static_cast<double>(clearColor_[1]),
                static_cast<double>(clearColor_[2]),
            };
        }

        while (!stopRequested_.load()) {
            std::vector<PointXYZ> points;
            std::vector<PointRGB> colors;
            {
                std::unique_lock<std::mutex> lock(dataMutex_);
                dataCv_.wait_for(lock, std::chrono::milliseconds(16), [&] {
                    return stopRequested_.load() || hasNewPoints_ || m_HasNewColorPoints;
                });
                if (stopRequested_.load()) {
                    break;
                }
                if (hasNewPoints_) {
                    points = std::move(latestPoints_);
                    latestPoints_.clear();
                    hasNewPoints_ = false;
                }

                if (m_HasNewColorPoints) {
                    points = std::move(latestPoints_);
                    latestPoints_.clear();
                    colors = std::move(latestColorsPoints_);
                    latestColorsPoints_.clear();
                    m_HasNewColorPoints = false;
                }
            }

            if (!points.empty()) {
                updatePointCloud_(points, colors);
            }

            if (!vis.PollEvents()) {
                break;
            }
            vis.UpdateRender();
        }
    } catch (...) {
        // Swallow all exceptions to prevent process termination from a background thread.
    }

    try {
        vis.DestroyVisualizerWindow();
    } catch (...) {
    }
}

void Renderer3D::updatePointCloud_(const std::vector<PointXYZ>& points, const std::vector<PointRGB>& colors) {
    // Copy input (matches your current approach)
    std::vector<PointXYZ> safePoints = points;
    std::vector<PointRGB> safeColorPoints = colors;
    bool includeColor = false;
    if (safePoints.empty()) {
        safePoints.push_back(PointXYZ{0.0f, 0.0f, 0.0f});
    }

    if (safeColorPoints.empty()) {
        safeColorPoints.push_back(PointRGB{0, 0, 0});
    } else {
        includeColor = true;
    }

    // ---- ROI crop inside plotting ----
    std::vector<PointXYZ> cropped;
    std::vector<PointRGB> croppedColor;
    cropped.reserve(safePoints.size());
    croppedColor.reserve(safeColorPoints.size());

    const int W = cloudWidth_;
    const int H = cloudHeight_;

    // Clamp ROI rect (defensive)
    const int u0 = std::max(0, std::min(roiU0_, W));
    const int u1 = std::max(0, std::min(roiU1_, W));
    const int v0 = std::max(0, std::min(roiV0_, H));
    const int v1 = std::max(0, std::min(roiV1_, H));

    for (size_t i = 0; i < safePoints.size(); i++) {
        const PointXYZ& p = safePoints[i];

        // Basic validity
        if (!std::isfinite(p.x) || !std::isfinite(p.y) || !std::isfinite(p.z)) continue;

        if (!roiEnable_) {
            cropped.push_back(p);
            if (includeColor && i < safeColorPoints.size()) {
                croppedColor.push_back(safeColorPoints[i]);
            }
            continue;
        }

        bool keep = true;

        switch (roiMode_) {
        case RoiMode::ImageRect: {
            // Requires that points are in row-major order matching disparity:
            // i = v*W + u, with total points = W*H (or at least aligned).
            // If your list is NOT row-major, do NOT use this mode.
            if (W > 0) {
                const int u = static_cast<int>(i % static_cast<size_t>(W));
                const int v = static_cast<int>(i / static_cast<size_t>(W));
                if (u < u0 || u >= u1 || v < v0 || v >= v1) keep = false;
            }
            break;
        }
        case RoiMode::AngleCone: {
            // Forward cone about +Z axis (camera space)
            // yaw = atan2(x,z), pitch = atan2(y,z)
            if (p.z <= 0.0f) { keep = false; break; }
            const float yaw   = std::atan2(p.x, p.z);
            const float pitch = std::atan2(p.y, p.z);
            if (std::abs(yaw) > roiMaxYawRad_) keep = false;
            if (std::abs(pitch) > roiMaxPitchRad_) keep = false;
            break;
        }
        case RoiMode::Box: {
            // Simple 3D box crop
            if (p.z < roiMinZ_ || p.z > roiMaxZ_) keep = false;
            if (std::abs(p.x) > roiMaxAbsX_) keep = false;
            if (std::abs(p.y) > roiMaxAbsY_) keep = false;
            break;
        }
        }

        if (keep)  {
            cropped.push_back(p);
            if (includeColor && i < safeColorPoints.size()) {
                croppedColor.push_back(safeColorPoints[i]);
            }
        }
    }

    if (cropped.empty()) {
        cropped.push_back(PointXYZ{0.0f, 0.0f, 0.0f});
    }

    if (croppedColor.empty()) {
        croppedColor.push_back(PointRGB{0, 0, 0});
    }

    // ---- Build Open3D cloud ----
    pcd->Clear();
    pcd->points_.reserve(cropped.size());
    pcd->colors_.reserve(cropped.size());

    // Robust z scaling for coloring
    std::vector<float> zValues;
    zValues.reserve(cropped.size());
    float zMin = std::numeric_limits<float>::infinity();
    float zMax = -std::numeric_limits<float>::infinity();
    for (const auto& p : cropped) {
        if (!std::isfinite(p.z)) continue;
        zValues.push_back(p.z);
        zMin = (std::min)(zMin, p.z);
        zMax = (std::max)(zMax, p.z);
    }

    float zLo = zMin, zHi = zMax;
    if (zValues.size() >= 20) {
        std::sort(zValues.begin(), zValues.end());
        const size_t loIdx = (zValues.size() - 1) * 5 / 100;
        const size_t hiIdx = (zValues.size() - 1) * 95 / 100;
        zLo = zValues[loIdx];
        zHi = zValues[hiIdx];
    }
    if (!std::isfinite(zLo) || !std::isfinite(zHi) || zHi <= zLo) {
        zLo = 0.15f;
        zHi = 12.0f;
    }
    const float zRange = zHi - zLo;

    // Reproject from 3D to 2D
    

    for (const auto& p : cropped) {
        pcd->points_.push_back(Eigen::Vector3d{
            static_cast<double>(p.x),
            static_cast<double>(p.y),
            static_cast<double>(p.z)   // <-- DO NOT divide by 10
        });

        const float tRaw = (p.z - zLo) / zRange;
        const float t = (std::max)(0.0f, (std::min)(1.0f, tRaw));  // normalized: 0 (close) to 1 (far)

        double r, g, b;
        if (!includeColor) {  // If we only want gradient
            r = 1.0 - t;

            if (t < 0.5) {
                g = 1.0 - 2* t;
            } else {
                g = 2* t - 1.0;
            }
            
            b = t;                     // blue: 0 (close) to 1 (far)
            pcd->colors_.push_back(Eigen::Vector3d{r, g, b});
        }
    }

    // Iterate through the color vector
    if (includeColor) {

        for (const auto& clr : croppedColor) {
            pcd->colors_.push_back(Eigen::Vector3d{clr.r / 255.0, clr.g  / 255.0, clr.b / 255.0});
        }

        // 1. Estimate Normals (essential for most methods)
        // pcd->EstimateNormals(open3d::geometry::KDTreeSearchParamHybrid(0.1, 30)); // example params

        // 2. Perform surface reconstruction using one of the methods
        // Example: Ball Pivoting
        std::vector<double> radii = {0.005, 0.01, 0.02, 0.04}; // example radii
        // mesh = open3d::geometry::TriangleMesh::CreateFromPointCloudBallPivoting(*pcd, radii);

        // 3. (Optional) Further processing, e.g., computing vertex normals for smooth shading
        // if (mesh && mesh->HasVertices()) {
        //     mesh->ComputeVertexNormals();
        // }
        
    }

    vis.UpdateGeometry(pcd);
    // if (includeColor && mesh) {
    //     vis.UpdateGeometry(mesh);
    // }

    if (!viewInitialized_.load()) {
        vis.ResetViewPoint(true);
        viewInitialized_.store(true);
    }
}


}  // namespace rc_car
