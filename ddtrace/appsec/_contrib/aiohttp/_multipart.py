import asyncio
import base64
import binascii
import contextlib
import json
from typing import Any
from typing import Union
import zlib

from aiohttp import hdrs
from aiohttp.multipart import BodyPartReader
from aiohttp.multipart import MultipartReader
from aiohttp.multipart import parse_content_disposition
from aiohttp.streams import StreamReader
from aiohttp.web import Request

from ddtrace.internal.utils.http import MediaType
from ddtrace.internal.utils.http import classify_media_type


async def multipart_fields(request: Request, raw_body: bytes, max_size: int) -> dict[str, Any]:
    stream = StreamReader(request.protocol, max(len(raw_body), 2**16), loop=asyncio.get_running_loop())
    stream.feed_data(raw_body)
    stream.feed_eof()
    return await _read_fields(MultipartReader(request.headers, stream), max_size)


async def _read_fields(reader: MultipartReader, max_size: int) -> dict[str, Any]:
    fields: dict[str, list[Any]] = {}
    while True:
        try:
            part = await reader.next()
            if part is None:
                break
            _, params = parse_content_disposition(part.headers.get(hdrs.CONTENT_DISPOSITION))
            if isinstance(part, MultipartReader):
                value: Any = await _read_fields(part, max_size)
            elif params.get("filename"):
                await part.release()
                continue
            else:
                value = _decode(part, bytes(await part.read()), max_size)
        except Exception:
            break
        fields.setdefault(params.get("name", ""), []).append(value)
    return {key: values[0] if len(values) == 1 else values for key, values in fields.items()}


def _decode(part: BodyPartReader, data: bytes, max_size: int) -> Union[str, Any]:
    with contextlib.suppress(Exception):
        transfer_encoding = part.headers.get(hdrs.CONTENT_TRANSFER_ENCODING, "").lower()
        if transfer_encoding == "base64":
            data = base64.b64decode(data)
        elif transfer_encoding == "quoted-printable":
            data = binascii.a2b_qp(data)
        content_encoding = part.headers.get(hdrs.CONTENT_ENCODING, "").lower()
        if content_encoding in ("deflate", "gzip"):
            wbits = 16 + zlib.MAX_WBITS if content_encoding == "gzip" else -zlib.MAX_WBITS
            data = zlib.decompressobj(wbits).decompress(data, max_size)
    try:
        text = data.decode(part.get_charset(default="utf-8"), errors="replace")
    except LookupError:
        text = data.decode("utf-8", errors="replace")
    if classify_media_type(part.headers.get(hdrs.CONTENT_TYPE)) is MediaType.JSON:
        try:
            return json.loads(text)
        except ValueError:
            pass
    return text
