from pathlib import Path
import subprocess

from setuptools import find_packages, setup


ROOT = Path(__file__).resolve().parent


def read_readme():
    return (ROOT / "README.md").read_text(encoding="utf-8")


def get_version():
    version_file = ROOT / "toffee" / "__version.py"
    if version_file.exists():
        about = {}
        exec(version_file.read_text(encoding="utf-8"), about)
        return about.get("__version__", "0.0.0")

    try:
        version = subprocess.check_output(
            ["git", "describe", "--tags", "--always"],
            cwd=ROOT,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "0.0.0"

    if version.startswith("v"):
        version = version[1:]

    if "-" not in version:
        return version

    tag, distance, commit = version.split("-", 2)
    return f"{tag}.dev{distance}+{commit}"


setup(
    name="pytoffee",
    version=get_version(),
    description="a framework for building hardware verification platform using software method",
    long_description=read_readme(),
    long_description_content_type="text/markdown",
    license="Apache-2.0",
    python_requires=">=3.8",
    packages=find_packages(
        exclude=("tests", "tests.*", "docs", "docs.*", "example", "example.*")
    ),
    include_package_data=True,
    install_requires=[],
    extras_require={
        "docs": [
            "sphinx",
            "sphinx-rtd-theme",
            "sphinx-autodoc-typehints",
            "myst-parser",
        ]
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Topic :: Scientific/Engineering :: Electronic Design Automation (EDA)",
        "License :: OSI Approved :: Apache Software License",
    ],
    url="https://github.com/XS-MLVP/toffee",
    project_urls={
        "Homepage": "https://github.com/XS-MLVP/toffee",
        "Source": "https://github.com/XS-MLVP/toffee",
        "Tracker": "https://github.com/XS-MLVP/toffee/issues",
    },
)
