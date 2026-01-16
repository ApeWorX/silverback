#----------------------------------------------------------#
# See LICENSE in the project root for license information. #
#----------------------------------------------------------#
ARG PYTHON_VERSION="3.11"
ARG BASE_APE_IMAGE="ghcr.io/apeworx/ape:python${PYTHON_VERSION}-stable-slim"

# Stage 1: Build dependencies
# NOTE: Build with builder image to reduce image size
FROM ${BASE_APE_IMAGE} as slim-builder

# NOTE: Switch back to root for building
USER root
WORKDIR /home/harambe/project

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/

# Only copy dependency files first (locked deps change less often)
# NOTE: In CI, you need to cache `uv.lock` (or create it if it doesn't exist)
COPY pyproject.toml uv.lock ./

# UV Configurations
# NOTE: use system python (better for our images, that inherit from `python:$VERSION`)
ENV UV_MANAGED_PYTHON=false
# NOTE: skip installing dev-only dependencies
ENV UV_NO_DEV=true
# NOTE: use `uv.lock` that we loaded into build
ENV UV_FROZEN=true
# NOTE: installs everything as non-editable (faster)
ENV UV_NO_EDITABLE=true
# NOTE: improves load speed of dependencies
ENV UV_COMPILE_BYTECODE=true
# NOTE: link mode "copy" silences warnings about hard links in other commands
ENV UV_LINK_MODE=copy

# Install dependencies first
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-project

# NOTE: Needed to mock version for `setuptools-scm` (pass at build time)
ARG VERSION
ENV SETUPTOOLS_SCM_PRETEND_VERSION_FOR_SILVERBACK=${VERSION}

# Now copy Silverback's source code over
COPY silverback silverback

# Install Silverback using pre-installed dependencies
# NOTE: --extra build to include build-only dependencies (for cloud use)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --extra build

# TODO: Figure out why `cluster/` subfolder isn't copying over from above command
RUN cp -r silverback/cluster .venv/lib/python3.11/site-packages/silverback/cluster

# Stage 2: Slim image (Based on Ape slim)

FROM ${BASE_APE_IMAGE} AS slim

COPY --from=slim-builder --chown=harambe:harambe \
    /home/harambe/project/.venv /home/harambe/project/.venv

# NOTE: Switch back to user again for final image
USER harambe

# Add the virtual environment to PATH so Silverback is callable
ENV PATH="/home/harambe/project/.venv/bin:$PATH"

# See version of Ape
RUN ape --version

# See version of Silverback
RUN silverback --version

ENTRYPOINT ["silverback"]
CMD ["--help"]

# Stage 3: Add plugins on top of slim-builder

FROM slim-builder AS full-builder

# Install recommended plugins
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --extra build --extra recommended-plugins

# Stage 4: Full image (slim with recommended plugins from full-builder)

FROM slim AS full

# Install anvil (for the Foundry plugin to be useful)
# NOTE: Adds 33MB to build
COPY --from=ghcr.io/foundry-rs/foundry:stable \
    /usr/local/bin/anvil /home/harambe/.local/bin/anvil

COPY --from=full-builder --chown=harambe:harambe \
    /home/harambe/project/.venv /home/harambe/project/.venv

# See state of Ape plugins
RUN ape plugins list

# NOTE: Use same WORKDIR, USER, ENTRYPOINT and CMD as slim
