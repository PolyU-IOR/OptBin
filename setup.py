"""PEP 517 builds compile the reader; converters are installed with CMake."""
import struct
import sys

from setuptools import Extension, setup

if sys.platform != "linux" or sys.byteorder != "little" or struct.calcsize("P") != 8:
    raise RuntimeError("OptBin currently supports 64-bit little-endian Linux builds")

setup(
    ext_modules=[Extension(
        "optbin._core",
        sources=["python/optbin/_core_module.c", "src/optbin.c", "src/validate.c",
                 "src/section_decode.c", "src/model_io.c", "src/bqp.c", "src/zstd_dyn.c"],
        include_dirs=["include", "src"],
        libraries=["z", "dl", "pthread", "m"],
        define_macros=[("_FILE_OFFSET_BITS", "64"), ("Py_LIMITED_API", "0x030A0000")],
        extra_compile_args=["-std=c11"],
        py_limited_api=True,
    )],
    options={"bdist_wheel": {"py_limited_api": "cp310"}},
)
