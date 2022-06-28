FROM python:3.9-slim AS build

WORKDIR /operator-build

RUN mkdir workspace && pip install --target workspace ddtrace

FROM busybox

COPY --from=build /operator-build/workspace /pypackages

RUN chmod -R go+r /pypackages
