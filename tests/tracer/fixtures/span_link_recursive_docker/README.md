# SpanLink Recursive Attribute Reproducer

This is a minimal standalone reproducer for the SpanLink recursive attribute
crash. Vulnerable builds recurse in the native attribute flattener until the
process exits with `SIGSEGV`.

Build and run:

```sh
docker build \
  -f tests/tracer/fixtures/span_link_recursive_docker/Dockerfile \
  -t ddtrace-span-link-recursive-repro \
  tests/tracer/fixtures/span_link_recursive_docker

docker run --rm ddtrace-span-link-recursive-repro
```

Expected result on vulnerable builds:

```text
calling SpanLink.to_dict(); vulnerable builds crash with SIGSEGV
Fatal Python error: Segmentation fault
```

Fixed builds should reject the recursive attributes with a Python exception and
exit `0`.

To test a specific released package, pass a pip requirement:

```sh
docker build \
  --build-arg 'DDTRACE_SPEC=ddtrace==<version>' \
  -f tests/tracer/fixtures/span_link_recursive_docker/Dockerfile \
  -t ddtrace-span-link-recursive-repro \
  tests/tracer/fixtures/span_link_recursive_docker
```
