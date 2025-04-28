#!/usr/bin/env python
# -*- coding: utf-8 -*-
from setuptools import find_packages, setup

extras_require = {
    "mcp": [
        "fastmcp",  # MCP Server & AI Computer Use
    ],
    "test": [  # `test` GitHub Action jobs uses this
        "pytest>=6.0",  # Core testing package
        "pytest-xdist",  # Multi-process runner
        "pytest-cov",  # Coverage analyzer plugin
        "hypothesis",  # Strategy-based fuzzer
        "hypothesis-jsonschema",  # Generate strategies for pydantic models
    ],
    "lint": [
        "black>=24.10.0,<25",  # Auto-formatter and linter
        "mypy>=1.13.0,<2",  # Static type analyzer
        "types-PyYAML",  # Needed for PyYAML
        "types-setuptools",  # Needed for mypy type shed
        "flake8>=7.1.1,<8",  # Style linter
        "isort>=5.13.2,<6",  # Import sorting linter
        "mdformat>=0.7.19",  # Auto-formatter for markdown
        "mdformat-gfm>=0.3.6",  # Needed for formatting GitHub-flavored markdown
        "mdformat-frontmatter>=2.0",  # Needed for frontmatters-style headers in issue templates
        "mdformat-pyproject>=0.0.2",  # Allows configuring in pyproject.toml
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
    + extras_require["mcp"]
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
        "apepay>=0.3.5,<1",
        "click",  # Use same version as eth-ape
        "eth-ape>=0.8.31,<1",
        "ethpm-types>=0.6.10",  # lower pin only, `eth-ape` governs upper pin
        "eth-pydantic-types",  # Use same version as eth-ape
        "exceptiongroup; python_version < '3.11'",  # Used with TaskGroup
        "packaging",  # Use same version as eth-ape
        "pycron>=3.1,<4",  # Checking/triggering cron tasks
        "pydantic_settings",  # Use same version as eth-ape
        "quattro>=25.2,<26",  # Manage task groups and background tasks
        "taskiq[metrics]>=0.11.16,<0.12",
        "tomlkit>=0.12,<1",  # For reading/writing global platform profile
        "fief-client[cli]>=0.19,<1",  # for platform auth/cluster login
        "web3>=7.7,<8",  # TODO: Remove when Ape v0.9 is released (Ape v0.8 allows web3 v6)
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
        "Programming Language :: Python :: 3.13",
    ],
)
