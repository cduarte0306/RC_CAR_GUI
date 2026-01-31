#include <iostream>
#include <filesystem>
#include <fstream>
#include <chrono>
#include <thread>
#include <vector>
#include <cstring>
#include <cmath>
#include <algorithm>
#include <limits>

#include "math_operations.h"
#include "renderer3d.h"

#ifdef RC_CAR_HAS_OPENCV
#include <opencv2/core.hpp>
#endif
#include <Eigen/Dense>

#include <open3d/Open3D.h>


#pragma pack(push, 1)
struct Header{
    char Magic[10];
    uint64_t length = 0;
};

struct PointXYZ {
    float x;
    float y;
    float z;
};

#pragma pack(pop)


std::vector<std::vector<PointXYZ>> unpackPcl(std::string filePath) {
    std::vector<std::vector<PointXYZ>> pclVideo;
    
    std::ifstream file(filePath, std::ios::in | std::ios::binary);

    if (!file.is_open()) {
        throw("Could not open file");
    }

    size_t fileSize = std::filesystem::file_size(filePath);

    std::vector<char> buffer(fileSize);
    file.read(buffer.data(), fileSize);
    file.close();

    // Read file contents 
    size_t offset = 0;
    while (offset + sizeof(Header) <= buffer.size()) {
        Header hdr{};
        std::memcpy(&hdr, buffer.data() + offset, sizeof(Header));

        if (std::memcmp(hdr.Magic, "POINTCLOUD", 10) != 0) break;

        offset += sizeof(Header);
        if (offset + hdr.length > buffer.size()) break;

        if (hdr.length == 0) {
            continue;
        }

        if (hdr.length % sizeof(PointXYZ) != 0) {
            // Unexpected payload size; bail to avoid desync.
            break;
        }

        // Copy into an aligned buffer (avoid undefined behavior from misaligned casts).
        const size_t pointCount = hdr.length / sizeof(PointXYZ);
        std::vector<PointXYZ> payload(pointCount);
        std::memcpy(payload.data(), buffer.data() + offset, hdr.length);

        // Filter invalid points (inf/nan) so Open3D won't choke on them.
        std::vector<PointXYZ> points;
        points.reserve(pointCount);
        for (size_t i = 0; i < pointCount; i++) {
            const PointXYZ& p = payload[i];
            if (!std::isfinite(p.x) || !std::isfinite(p.y) || !std::isfinite(p.z)) {
                continue;
            }

            // std::cout << "Points-> X=" << p.x << " Y=" << p.y << " Z=" << p.z << "\r\n"; 
            points.push_back(p);
        }

        // Add to video
        pclVideo.push_back(points);
        offset += hdr.length;
    }

    return pclVideo;
}


int main(int argc, char** argv) {
    std::cout << "=== RC Car C++ Point Cloud Demo ===" << std::endl;

    if (argc < 2) {
        std::cerr << "Usage: " << argv[0] << std::endl;
        return -1;
    }
    const char* pclFilePath = argv[1];
    std::cout << "Point Cloud File Path: " << pclFilePath << std::endl;

    if (!std::filesystem::exists(pclFilePath)) {
        std::cerr << "Error: Point cloud file does not exist: " << pclFilePath << std::endl;
        return -1;
    }

    std::string fileName = std::string(pclFilePath);
    if (fileName.find(".pcl") == std::string::npos) {
        std::cerr << "Error: Wrong file type provided\r\n";
        return -1;
    }

    auto frames = unpackPcl(fileName);
    if (frames.empty()) {
        std::cerr << "No point cloud frames found in file." << std::endl;
        return -1;
    }

    auto pcd = std::make_shared<open3d::geometry::PointCloud>();

    // Visualize the point cloud
    open3d::visualization::Visualizer vis;
    // Position the window near the top of the primary screen so it doesn't open "too low".
    if (!vis.CreateVisualizerWindow("Live Point Cloud", 1280, 720, 80, 30)) {
        std::cerr << "Failed to open Visalizer window\r\n";
        exit(-1);
    }
    vis.AddGeometry(pcd);
    vis.GetRenderOption().point_size_ = 2.0;

    std::cout << "Live updating display... (close window to quit)" << std::endl;
    std::vector<PointXYZ> frame;

    std::cout << "Opening visualization window... (close window to quit)" << std::endl;
    bool view_initialized = false;
    while (true) {
        for (const auto& currFrame : frames) {
            pcd->Clear();
            pcd->points_.reserve(currFrame.size());
            pcd->colors_.reserve(currFrame.size());

            float zMin = std::numeric_limits<float>::infinity();
            float zMax = -std::numeric_limits<float>::infinity();
            for (const auto& p : currFrame) {
                zMin = (std::min)(zMin, p.z);
                zMax = (std::max)(zMax, p.z);
            }
            const float zRange = (std::isfinite(zMin) && std::isfinite(zMax) && zMax > zMin) ? (zMax - zMin) : 1.0f;

            for (const auto& p : currFrame) {
                pcd->points_.push_back((Eigen::Vector3d{
                    static_cast<double>(p.x), 
                    static_cast<double>(p.y), 
                    static_cast<double>(p.z)}
                ));
                const float t = (p.z - zMin) / zRange;
                double r = static_cast<double>(t);
                if (r < 0.0) r = 0.0;
                if (r > 1.0) r = 1.0;
                const double b = 1.0 - r;
                pcd->colors_.push_back({r, 0.0, b});
            }

            if (!view_initialized) {
                vis.ResetViewPoint(true);
                view_initialized = true;
            }

            vis.UpdateGeometry(pcd);
            if (!vis.PollEvents()) break;;
            vis.UpdateRender();

            std::this_thread::sleep_for(std::chrono::milliseconds(250));
        }
    }

    
    std::cout << "Visualization closed" << std::endl;
    return 0;
}
