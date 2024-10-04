#!/usr/bin/env python
# -*- coding: utf-8 -*-
from setuptools import find_packages, setup

extras_require = {
    "test": [  # `test` GitHub Action jobs uses this
        "pytest>=6.0",  # Core testing package
        "pytest-xdist",  # Multi-process runner
        "pytest-cov",  # Coverage analyzer plugin
        "hypothesis",  # Strategy-based fuzzer
        "hypothesis-jsonschema",  # Generate strategies for pydantic models
    ],
    "lint": [
        "black>=24",  # Auto-formatter and linter
        "mypy>=1.10",  # Static type analyzer
        "types-setuptools",  # Needed for mypy type shed
        "flake8>=7",  # Style linter
        "isort>=5.13",  # Import sorting linter
        "mdformat>=0.7",  # Auto-formatter for markdown
        "mdformat-gfm>=0.3.6",  # Needed for formatting GitHub-flavored markdown
        "mdformat-frontmatter>=2.0",  # Needed for frontmatters-style headers in issue templates
        "mdformat-pyproject>=0.0.1",  # Allows configuring in pyproject.toml
    ],
    "doc": ["sphinx-ape"],
    "release": [  # `release` GitHub Action job uses this
        "setuptools",  # Installation tool
        "wheel",  # Packaging tool
        "twine",  # Package upload tool
    ],
    "dev": [
        "commitizen",  # Manage commits and publishing releases
        "pre-commit",  # Ensure that linters are run prior to committing
        "pytest-watch",  # `ptw` test watcher/runner
        "IPython",  # Console for interacting
        "ipdb",  # Debugger (Must use `export PYTHONBREAKPOINT=ipdb.set_trace`)
    ],
}

# NOTE: `pip install -e .[dev]` to install package
extras_require["dev"] = (
    extras_require["test"]
    + extras_require["lint"]
    + extras_require["doc"]
    + extras_require["release"]
    + extras_require["dev"]
)

with open("./README.md") as readme:
    long_description = readme.read()


setup(
    name="silverback",
    use_scm_version=True,
    setup_requires=["setuptools_scm"],
    description="""Ape SDK for the Silverback platform""",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="ApeWorX Ltd.",
    author_email="admin@apeworx.io",
    url="https://github.com/ApeWorX/silverback",
    include_package_data=True,
    install_requires=[
        "click",  # Use same version as eth-ape
        "eth-ape>=0.7,<1.0",
        "ethpm-types>=0.6.10",  # lower pin only, `eth-ape` governs upper pin
        "eth-pydantic-types",  # Use same version as eth-ape
        "packaging",  # Use same version as eth-ape
        "pydantic_settings",  # Use same version as eth-ape
        "taskiq[metrics]>=0.11.3,<0.12",
        "tomlkit>=0.12,<1",  # For reading/writing global platform profile
        "fief-client[cli]>=0.19,<1",  # for platform auth/cluster login
    ],
    entry_points={
        "console_scripts": ["silverback=silverback._cli:cli"],
    },
    python_requires=">=3.10,<4",
    extras_require=extras_require,
    py_modules=["silverback"],
    license="Apache-2.0",
    zip_safe=False,
    keywords="ethereum",
    packages=find_packages(exclude=["tests", "tests.*"]),
    package_data={"silverback": ["py.typed"]},
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: Apache Software License",
        "Natural Language :: English",
        "Operating System :: MacOS",
        "Operating System :: POSIX",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)
