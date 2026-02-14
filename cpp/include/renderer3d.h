#pragma once

#include <atomic>
#include <condition_variable>
#include <mutex>
#include <thread>
#include <array>
#include <vector>

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
    void setClearColor(float r, float g, float b, float a = 1.0f);
    void enableVisualizerWindow(bool enable = true);

private:

    struct PointXYZ {
        float x;
        float y;
        float z;
    };

    void renderLoop_();
    void updatePointCloud_(const std::vector<PointXYZ>& points);

    std::array<float, 4> clearColor_;

    std::mutex dataMutex_;
    std::condition_variable dataCv_;
    std::vector<PointXYZ> latestPoints_;
    bool hasNewPoints_ = false;

    std::atomic<bool> stopRequested_{false};
    std::atomic<bool> visualizerEnabled_{false};
    std::atomic<bool> viewInitialized_{false};

    std::shared_ptr<open3d::geometry::PointCloud> pcd;
    open3d::visualization::Visualizer vis;
    std::thread m_RenderThread_;
};

}  // namespace rc_car
