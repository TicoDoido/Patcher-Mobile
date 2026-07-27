from setuptools import Extension, setup
from Cython.Build import cythonize

setup(
    name="bsdiff-cython-android",
    version="0.1.0",
    ext_modules=cythonize(
        [
            Extension("bsdiff_cython", ["bsdiff_cython.pyx"]),
        ],
        language_level=3,
    ),
)
