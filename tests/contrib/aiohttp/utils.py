import pkg_resources

if pkg_resources.parse_version(aiohttp.__version__) >= pkg_resources.parse_version('3.3.0'):
    AIOHTTP_33x = True
else:
    AIOHTTP_33x = False
