#---------------------------------------------------------------------------------------------
# See LICENSE in the project root for license information.
#---------------------------------------------------------------------------------------------

# Build with builder image to reduce image size
FROM python:3.10 as builder
USER root
WORKDIR /wheels
COPY . .
# upgrade pip and install wheel
RUN pip install --upgrade pip && pip install wheel
# install silverback
RUN pip wheel . --wheel-dir=/wheels

# Install from wheels
FROM apeworx/ape:stable
USER root
COPY --from=builder /wheels /wheels
RUN pip install --upgrade pip \
    && pip install silverback --no-cache-dir --find-links=/wheels 
USER harambe

ENTRYPOINT ["silverback"]
