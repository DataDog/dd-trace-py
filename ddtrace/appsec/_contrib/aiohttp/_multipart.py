import base64
import binascii
import json
from typing import Any
from typing import Union
import zlib

from aiohttp import hdrs
from aiohttp.helpers import parse_mimetype
from aiohttp.multipart import BodyPartReader
from aiohttp.multipart import MultipartReader
from aiohttp.multipart import content_disposition_filename
from aiohttp.multipart import parse_content_disposition
from aiohttp.web import Request

from ddtrace.contrib.internal.aiohttp.middlewares import _restore_request_body
from ddtrace.internal.utils.http import MediaType
from ddtrace.internal.utils.http import classify_media_type


class _Budget:
    __slots__ = ("remaining",)

    def __init__(self, size: int) -> None:
        self.remaining = size


async def multipart_fields(request: Request, raw_body: bytes, max_size: int) -> dict[str, Any]:
    _restore_request_body(request, raw_body)
    return await _read_fields(await request.multipart(), _Budget(max_size))


async def _read_fields(reader: MultipartReader, budget: _Budget) -> dict[str, Any]:
    fields: dict[str, list[Any]] = {}
    while True:
        try:
            part = await reader.next()
            if part is None:
                break
            if isinstance(part, MultipartReader):
                _, params = parse_content_disposition(part.headers.get(hdrs.CONTENT_DISPOSITION))
                name = content_disposition_filename(params, "name")
                value: Any = await _read_fields(part, budget)
            elif part.filename:
                await part.release()
                continue
            else:
                name = part.name
                value = _decode(part, await part.read(), budget)
        except Exception:
            break
        fields.setdefault(name or "", []).append(value)
    return {key: values[0] if len(values) == 1 else values for key, values in fields.items()}


def _decode(part: BodyPartReader, data: Union[bytes, bytearray], budget: _Budget) -> Any:
    try:
        return _decode_as_aiohttp(part, data, budget)
    except Exception:
        return data.decode("utf-8", errors="replace")


def _decode_as_aiohttp(part: BodyPartReader, data: Union[bytes, bytearray], budget: _Budget) -> Any:
    transfer_encoding = part.headers.get(hdrs.CONTENT_TRANSFER_ENCODING, "").lower()
    if transfer_encoding == "base64":
        data = base64.b64decode(data)
    elif transfer_encoding == "quoted-printable":
        data = binascii.a2b_qp(data)
    content_encoding = part.headers.get(hdrs.CONTENT_ENCODING, "").lower()
    needs_content_decoding = getattr(part, "_needs_content_decoding", lambda: True)
    if content_encoding in ("deflate", "gzip") and needs_content_decoding() and budget.remaining > 0:
        wbits = 16 + zlib.MAX_WBITS if content_encoding == "gzip" else -zlib.MAX_WBITS
        data = zlib.decompressobj(wbits).decompress(data, budget.remaining)
        budget.remaining -= len(data)
    content_type = part.headers.get(hdrs.CONTENT_TYPE)
    if classify_media_type(content_type) is MediaType.JSON:
        charset = parse_mimetype(content_type or "").parameters.get("charset")
        return json.loads(data if charset is None else data.decode(charset))
    return data.decode(part.get_charset(default="utf-8"), errors="replace")
