#----------------------------------------------------------#
# See LICENSE in the project root for license information. #
#----------------------------------------------------------#
ARG BASE_APE_IMAGE="ghcr.io/apeworx/ape:stable-slim"

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

# NOTE: Needed to mock version for `setuptools-scm` (pass at build time)
ARG VERSION
ENV SETUPTOOLS_SCM_PRETEND_VERSION_FOR_SILVERBACK=${VERSION}

# NOTE: link mode "copy" silences warnings about hard links in other commands
ENV UV_LINK_MODE=copy

# Install dependencies first
# NOTE: --compile-bytecode improves load speed of dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-editable --compile-bytecode --no-install-project

# Now copy Silverback's source code over
COPY silverback silverback

# Install Silverback using pre-installed dependencies
# NOTE: --compile-bytecode improves load speed of dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-editable --compile-bytecode --extra build

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
    uv sync --frozen --no-editable --compile-bytecode --extra build --extra recommended-plugins

# Stage 4: Full image (slim with recommended plugins from full-builder)

FROM slim AS full

# Install anvil (for the Foundry plugin to be useful)
# NOTE: Adds 33MB to build
COPY --from=ghcr.io/foundry-rs/foundry:latest \
    /usr/local/bin/anvil /home/harambe/.local/bin/anvil

COPY --from=full-builder --chown=harambe:harambe \
    /home/harambe/project/.venv /home/harambe/project/.venv

# See state of Ape plugins
RUN ape plugins list

# NOTE: Use same WORKDIR, USER, ENTRYPOINT and CMD as slim
