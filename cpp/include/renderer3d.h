#pragma once

// Prevent windows.h from defining min/max macros that conflict with std::min/max
#ifndef NOMINMAX
#define NOMINMAX
#endif

#include <atomic>
#include <condition_variable>
#include <chrono>
#include <cstdint>
#include <mutex>
#include <thread>
#include <array>
#include <vector>

#ifdef _WIN32
#include <windows.h>
#endif

#ifdef RC_CAR_HAS_OPEN3D
#include <open3d/Open3D.h>
#endif

namespace rc_car {

/**
 * Minimal OpenGL-based renderer for showcasing 3D content.
 *
 * Rendering calls assume an active OpenGL context (provided by the caller).
 */
class Renderer3D {
public:
    Renderer3D();
    ~Renderer3D();
    
    void setPointCloudData(char* pcData, size_t numPoints);
    void setPointCloudColorData(char* pcData, char* rgbData, size_t numPoints);
    void setClearColor(float r, float g, float b, float a = 1.0f);
    void setCloudDimensions(int width, int height);
    void enableVisualizerWindow(bool enable = true);
    std::uintptr_t GetWindowId() const;

    // GUI-thread-driven lifecycle for embedding the window inside Qt.
    // startWindow(), pump() and stopWindow() MUST all be called from the same
    // thread (the Qt GUI thread). Creating and pumping the window on the GUI
    // thread keeps the native window owned by that thread, so reparenting it via
    // embedInto()/SetParent does not cross threads (which is what caused the
    // input-queue attachment freeze and the window churn).
    bool startWindow();   // Create the window + geometry. Idempotent. Returns success.
    bool pump();          // One PollEvents()/UpdateRender() step. False if window closed.
    void stopWindow();    // Destroy the window. Idempotent.
    void embedInto(std::uintptr_t parentHandle);  // Reparent as WS_CHILD of parentHandle (HWND).

    // Optional: expensive surface reconstruction from point cloud.
    // Disabled by default because reconstruction can be costly.
    void setMeshReconstructionEnabled(bool enable);
    void setMeshReconstructionIntervalMs(int intervalMs);

private:

    struct PointXYZ {
        float x;
        float y;
        float z;
    };

    struct PointRGB {
        uint32_t r;
        uint32_t g;
        uint32_t b;
    };

    // In Renderer3D class (public or private with setters)
    int cloudWidth_  = 0;
    int cloudHeight_ = 0;

    // ROI controls
    bool roiEnable_ = true;

    // Image ROI (fractional, 0.0–1.0, mapped to cloudWidth_/cloudHeight_)
    float roiU0Frac_ = 0.25f;  // left
    float roiU1Frac_ = 0.75f;  // right
    float roiV0Frac_ = 0.19f;  // top
    float roiV1Frac_ = 0.81f;  // bottom

    // Angle ROI (radians)
    float roiMaxYawRad_   = 20.0f * 3.14159265f / 180.0f;
    float roiMaxPitchRad_ = 15.0f * 3.14159265f / 180.0f;

    // 3D box ROI (meters)
    float roiMinZ_ = 0.15f;
    float roiMaxZ_ = 12.0f;
    float roiMaxAbsX_ = 3.0f;
    float roiMaxAbsY_ = 2.0f;

    // Choose mode
    enum class RoiMode { ImageRect, AngleCone, Box };
    RoiMode roiMode_ = RoiMode::AngleCone;

    void renderLoop_();
    void updatePointCloud_(const std::vector<PointXYZ>& points, const std::vector<PointRGB>& colors);

    void meshLoop_();

    std::array<float, 4> clearColor_;

    std::mutex dataMutex_;
    std::condition_variable dataCv_;
    std::vector<PointXYZ> latestPoints_;
    std::vector<PointRGB> latestColorsPoints_;
    bool hasNewPoints_ = false;
    bool m_HasNewColorPoints = false;

    std::atomic<bool> stopRequested_{false};
    std::atomic<bool> visualizerEnabled_{false};
    std::atomic<bool> viewInitialized_{false};

    // GUI-thread embedded-window state (see startWindow/pump/stopWindow).
    std::uintptr_t windowHandle_ = 0;       // Native handle of the visualizer window (0 if none).
    std::atomic<bool> windowActive_{false};

    // Mesh reconstruction worker state.
    std::atomic<bool> meshStopRequested_{false};
    std::atomic<bool> meshEnabled_{false};
    std::atomic<int> meshIntervalMs_{750};
    std::thread meshThread_;
    std::mutex meshMutex_;
    std::condition_variable meshCv_;
    std::vector<PointXYZ> meshInputPoints_;
    bool meshJobPending_ = false;
    bool meshHasNewResult_ = false;
    std::shared_ptr<open3d::geometry::TriangleMesh> meshResult_;
    std::chrono::steady_clock::time_point lastMeshRequest_{};

    std::shared_ptr<open3d::geometry::PointCloud> pcd;
    std::shared_ptr<open3d::geometry::TriangleMesh> mesh;
    open3d::visualization::Visualizer vis;
    std::thread m_RenderThread_;

    static HWND FindHwndByExactTitle(const wchar_t* title) {
#ifdef _WIN32
        return ::FindWindowW(nullptr, title);
#else
        return nullptr;
#endif
    }
};

}  // namespace rc_car
