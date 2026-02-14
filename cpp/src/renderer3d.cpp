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
    : pcd(std::make_shared<open3d::geometry::PointCloud>()) {
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
            {
                std::unique_lock<std::mutex> lock(dataMutex_);
                dataCv_.wait_for(lock, std::chrono::milliseconds(16), [&] {
                    return stopRequested_.load() || hasNewPoints_;
                });
                if (stopRequested_.load()) {
                    break;
                }
                if (hasNewPoints_) {
                    points = std::move(latestPoints_);
                    latestPoints_.clear();
                    hasNewPoints_ = false;
                }
            }

            if (!points.empty()) {
                updatePointCloud_(points);
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

void Renderer3D::updatePointCloud_(const std::vector<PointXYZ>& points) {
    std::vector<PointXYZ> safePoints = points;
    if (safePoints.empty()) {
        safePoints.push_back(PointXYZ{0.0f, 0.0f, 0.0f});
    }

    pcd->Clear();
    pcd->points_.reserve(safePoints.size());
    pcd->colors_.reserve(safePoints.size());

    std::vector<float> zValues;
    zValues.reserve(safePoints.size());
    float zMin = std::numeric_limits<float>::infinity();
    float zMax = -std::numeric_limits<float>::infinity();
    for (const auto& p : safePoints) {
        if (!std::isfinite(p.z)) {
            continue;
        }
        zValues.push_back(p.z);
        zMin = (std::min)(zMin, p.z);
        zMax = (std::max)(zMax, p.z);
    }

    float zLo = zMin;
    float zHi = zMax;
    if (zValues.size() >= 20) {
        std::sort(zValues.begin(), zValues.end());
        const size_t loIdx = (zValues.size() - 1) * 5 / 100;
        const size_t hiIdx = (zValues.size() - 1) * 95 / 100;
        zLo = zValues[loIdx];
        zHi = zValues[hiIdx];
    }

    if (!std::isfinite(zLo) || !std::isfinite(zHi) || zHi <= zLo) {
        zLo = 0.15f;
        zHi = 45.0f;
    }
    const float zRange = zHi - zLo;
    
    int nb_neighbors = 10;
    double std_ratio = 1.0;

    for (const auto& p : safePoints) {
        pcd->points_.push_back(Eigen::Vector3d{
            static_cast<double>(p.x),
            static_cast<double>(p.y),
            static_cast<double>(p.z),
        });

        const float tRaw = (p.z - zLo) / zRange;
        const float t = (std::max)(0.0f, (std::min)(1.0f, tRaw));  // normalized: 0 (close) to 1 (far)
        const double r = 1.0 - t;               // red: 1 (close) to 0 (far)
        const double g = t;                     // green: 0 (close) to 1 (far)
        const double b = t;                     // blue: 0 (close) to 1 (far)
        pcd->colors_.push_back(Eigen::Vector3d{r, g, b});
    }
    // pcd->RemoveStatisticalOutliers(nb_neighbors, std_ratio);
    vis.UpdateGeometry(pcd);
    if (!viewInitialized_.load()) {
        vis.ResetViewPoint(true);
        viewInitialized_.store(true);
    }
}


}  // namespace rc_car
