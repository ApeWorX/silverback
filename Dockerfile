#---------------------------------------------------------------------------------------------
# See LICENSE in the project root for license information.
#---------------------------------------------------------------------------------------------

# Build with builder image to reduce image size
FROM apeworx/ape:latest as builder
USER root
WORKDIR /wheels
COPY . .
RUN pip install --upgrade pip \
    && pip install wheel \
    && pip wheel silverback --wheel-dir=/wheels

# Install from wheels
FROM apeworx/ape:latest
USER root
COPY --from=builder /wheels /wheels
RUN pip install --upgrade pip \
    pip install . --no-cache-dir --find-links=/wheels
USER harambe

ENTRYPOINT ["silverback"]
