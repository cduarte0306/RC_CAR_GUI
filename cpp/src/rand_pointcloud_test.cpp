#include <Open3D/Open3D.h>
#include <random>
#include <vector>

int main() {
    using namespace open3d;

    // 1) Create a point cloud
    auto pcd = std::make_shared<geometry::PointCloud>();

    // 2) Fill it with random points (uniform in a cube)
    constexpr int kNumPoints = 5000;

    std::mt19937 rng(42); // fixed seed for repeatability
    std::uniform_real_distribution<double> dist(-1.0, 1.0);

    pcd->points_.reserve(kNumPoints);
    pcd->colors_.reserve(kNumPoints);

    for (int i = 0; i < kNumPoints; ++i) {
        double x = dist(rng);
        double y = dist(rng);
        double z = dist(rng);
        pcd->points_.emplace_back(x, y, z);

        // Simple color mapping: normalize [-1,1] -> [0,1]
        Eigen::Vector3d c((x + 1.0) * 0.5, (y + 1.0) * 0.5, (z + 1.0) * 0.5);
        pcd->colors_.push_back(c);
    }

    // 3) (Optional) estimate normals for nicer shading
    pcd->EstimateNormals(
        geometry::KDTreeSearchParamHybrid(/*radius=*/0.2, /*max_nn=*/30));

    // 4) Visualize
    visualization::DrawGeometries({pcd}, "Open3D: Random Point Cloud");

    return 0;
}