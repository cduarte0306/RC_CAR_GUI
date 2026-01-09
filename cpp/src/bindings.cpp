#include <pybind11/pybind11.h>
#include "math_operations.h"

namespace py = pybind11;

PYBIND11_MODULE(rc_car_cpp, m) {
    m.doc() = "RC Car C++ high-performance extensions";

    py::class_<rc_car::MathOperations>(m, "MathOperations")
        .def_static("vector_magnitude", &rc_car::MathOperations::vectorMagnitude,
                   py::arg("x"), py::arg("y"), py::arg("z"),
                   "Calculate the magnitude of a 3D vector")
        .def_static("angle_between_vectors", &rc_car::MathOperations::angleBetweenVectors,
                   py::arg("x1"), py::arg("y1"), py::arg("z1"),
                   py::arg("x2"), py::arg("y2"), py::arg("z2"),
                   "Calculate angle between two 3D vectors in radians")
        .def_static("normalize_vector", 
                   [](double x, double y, double z) {
                       rc_car::MathOperations::normalizeVector(x, y, z);
                       return py::make_tuple(x, y, z);
                   },
                   py::arg("x"), py::arg("y"), py::arg("z"),
                   "Normalize a 3D vector, returns (x, y, z) tuple");

    // Version information
    m.attr("__version__") = "1.0.0";
}
