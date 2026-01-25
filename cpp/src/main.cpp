#include <iostream>
#include "math_operations.h"
#include "renderer3d.h"

#ifdef RC_CAR_HAS_OPENCV
#include <opencv2/core.hpp>
#endif


int main(int argc, char** argv) {
    std::cout << "RC Car C++ app (debug stub)" << std::endl;
    std::cout << "Args: " << argc << std::endl;

    // Placeholder math call to keep the core library linked.
    double mag = rc_car::MathOperations::vectorMagnitude(1.0, 2.0, 3.0);
    std::cout << "vectorMagnitude(1,2,3) = " << mag << std::endl;

#ifdef RC_CAR_HAS_OPENCV
    std::cout << "OpenCV version: " << CV_VERSION << std::endl;
    cv::Mat demo(2, 2, CV_8UC3, cv::Scalar(10, 20, 30));
    std::cout << "OpenCV demo mat size: " << demo.rows << "x" << demo.cols << std::endl;
#else
    std::cout << "OpenCV not found at configure time; skipping demo." << std::endl;
#endif

    // TODO: replace with OpenGL window/context setup and renderer loop.
    return 0;
}
