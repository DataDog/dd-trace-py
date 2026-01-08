from setuptools import setup, Extension

setup(
    name="ubsantest",
    ext_modules=[
        Extension(
            "ubsantest",
            sources=["ubsan.c"],
            extra_compile_args=["-fsanitize=undefined", "-O1", "-fno-omit-frame-pointer"],
            extra_link_args=["-fsanitize=undefined"],
        )
    ],
)
