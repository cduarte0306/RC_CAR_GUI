#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>

#include "math_operations.h"
#include "renderer3d.h"

namespace py = pybind11;

PYBIND11_MODULE(rc_car_cpp, m) {
    m.doc() = "RC Car C++ high-performance extensions";

    py::class_<rc_car::Renderer3D>(m, "Renderer3D")
        .def(py::init<>())
        .def("setPointCloudData", [](rc_car::Renderer3D& self, 
                                    py::array_t<uint8_t> data, 
                                    size_t numPoints) {
            auto buf = data.request();
            char* buffer = static_cast<char*>(buf.ptr);
            self.setPointCloudData(buffer, numPoints);
        },
            py::arg("pcData"), py::arg("numPoints"),
            "Render a colored point cloud from numpy array or bytes")
        .def("setPointCloudColorData", [](rc_car::Renderer3D& self, 
                                    py::array_t<uint8_t> coordinates,
                                    py::array_t<uint8_t> rgb, 
                                    size_t numPoints) {
            auto pcBuf = coordinates.request();
            auto colorBuf = rgb.request();
            char* buffer = static_cast<char*>(pcBuf.ptr);
            char* rgbBuffer = static_cast<char*>(colorBuf.ptr);
            self.setPointCloudColorData(buffer, rgbBuffer, numPoints);
        },
            py::arg("coordinates"), py::arg("rgb"), py::arg("numPoints"),
            "Render a RGB point cloud from numpy array or bytes")
        .def("set_clear_color", &rc_car::Renderer3D::setClearColor,
            py::arg("r"), py::arg("g"), py::arg("b"), py::arg("a") = 1.0f,
            "Set the background clear color (RGBA)")
        .def("enable_visualizer_window", &rc_car::Renderer3D::enableVisualizerWindow,
            py::arg("enable") = true,
            "Enable/disable Open3D visualizer window (only for standalone, not PyQt)");

    // Version information
    m.attr("__version__") = "1.0.0";
}
