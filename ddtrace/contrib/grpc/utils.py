def parse_method_path(method_path):
    # unpack method path based on "/{package}.{service}/{method}"
    # first remove leading "/" as unnecessary
    package_service, method_name = method_path.lstrip('/').rsplit('/', 1)
    method_package, method_service = package_service.rsplit('.', 1)

    return method_package, method_service, method_name
