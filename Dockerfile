#---------------------------------------------------------------------------------------------
# See LICENSE in the project root for license information.
#---------------------------------------------------------------------------------------------

# Build with builder image to reduce image size
ARG BASE_APE_IMAGE_TAG
FROM python:3.11 as builder
USER root
WORKDIR /wheels
COPY . .
# upgrade pip and install wheel
RUN pip install --upgrade pip && pip install wheel
# install silverback
RUN pip wheel . --wheel-dir=/wheels

# Install from wheels
FROM ghcr.io/apeworx/ape:${BASE_APE_IMAGE_TAG:-latest-slim}
USER root
COPY --from=builder /wheels /wheels
RUN pip install --upgrade pip \
    && pip install silverback --no-cache-dir --find-links=/wheels 
USER harambe

ENTRYPOINT ["silverback"]
