import typing

class Package(typing.TypedDict):
    name: str
    version: str
    kind: typing.Literal["library"]
    paths: typing.List[str]

def build_libraries(filenames: typing.List[str]) -> typing.List[Package]: ...
def serialize_to_compressed_bytes(libs: typing.List[Package]) -> bytes: ...
