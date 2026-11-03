# syntax=docker/dockerfile:1
ARG RUST_IMAGE=rust:1.85-slim-bookworm
ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${RUST_IMAGE} AS rust
FROM ${PYTHON_IMAGE} AS builder
COPY --from=rust /usr/local/rustup /usr/local/rustup
COPY --from=rust /usr/local/cargo /usr/local/cargo
ENV RUSTUP_HOME=/usr/local/rustup CARGO_HOME=/usr/local/cargo PATH=/usr/local/cargo/bin:$PATH
RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*
COPY sdl /framework/sdl
COPY ose /framework/ose
COPY vibration-watch /app
ENV FRAMEWORK_ROOT=/framework SDL_ROOT=/framework/sdl
WORKDIR /app
ARG COMPONENT=observer
ARG SCENARIO=increasing-vibration
ARG ROUTING=mixed
RUN python -m pip wheel --no-deps --wheel-dir /wheels /framework/ose
RUN make -C /framework/sdl filter-python PYTHON=python
RUN python tools/vibration.py build --profile cluster --component "$COMPONENT" --scenario "$SCENARIO" --routing "$ROUTING"

FROM ${PYTHON_IMAGE} AS runtime
ENV FRAMEWORK_ROOT=/framework SDL_ROOT=/framework/sdl
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels ose && rm -rf /wheels
COPY --from=builder --chown=10001:10001 /app /app
COPY --from=builder --chown=10001:10001 /framework /framework
USER 10001:10001
ENTRYPOINT ["ose"]
