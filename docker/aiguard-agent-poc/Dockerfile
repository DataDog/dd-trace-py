# Builds ddtrace from this checkout so the POC runs against the local tracer code.
# Build context is the repository root (see docker-compose.yml).
FROM python:3.12-bookworm AS build

RUN curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
ENV PATH="/root/.cargo/bin:${PATH}"
ENV DD_FAST_BUILD=1

COPY . /src
# The native build tree is several GB: drop it in the same layer so it never reaches an image.
RUN pip wheel --no-cache-dir --no-deps -w /wheels /src && rm -rf /src

FROM python:3.12-slim-bookworm

COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

WORKDIR /poc
CMD ["python", "/poc/poc.py"]
