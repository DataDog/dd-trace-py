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
from aiohttp.multipart import content_disposition_filename
from aiohttp.multipart import parse_content_disposition
from aiohttp.web import Request

from ddtrace.appsec._ddwaf.ddwaf_types import DDWAF_MAX_STRING_LENGTH
from ddtrace.contrib.internal.aiohttp.middlewares import _restore_request_body
from ddtrace.internal.utils.http import MediaType
from ddtrace.internal.utils.http import classify_media_type


_MAX_DECODED_SIZE = 4 * DDWAF_MAX_STRING_LENGTH


async def multipart_fields(request: Request, raw_body: bytes) -> dict[str, Any]:
    _restore_request_body(request, raw_body)
    return await _read_fields(await request.multipart())


async def _read_fields(reader: MultipartReader) -> dict[str, Any]:
    fields: dict[str, list[Any]] = {}
    while True:
        try:
            part = await reader.next()
            if part is None:
                break
            if isinstance(part, MultipartReader):
                _, params = parse_content_disposition(part.headers.get(hdrs.CONTENT_DISPOSITION))
                name = content_disposition_filename(params, "name")
                value: Any = await _read_fields(part)
            elif part.filename:
                await part.release()
                continue
            else:
                name = part.name
                value = _decode(part, await part.read())
        except Exception:
            break
        fields.setdefault(name or "", []).append(value)
    return {key: values[0] if len(values) == 1 else values for key, values in fields.items()}


def _decode(part: BodyPartReader, data: Union[bytes, bytearray]) -> Any:
    with contextlib.suppress(Exception):
        data = _transfer_decode(part, data)
    with contextlib.suppress(Exception):
        data = _content_decode(part, data)
    charset = part.get_charset(default="")
    if classify_media_type(part.headers.get(hdrs.CONTENT_TYPE)) is MediaType.JSON:
        with contextlib.suppress(Exception):
            return json.loads(data.decode(charset) if charset else data)
    try:
        return data.decode(charset or "utf-8", errors="replace")
    except Exception:
        return data.decode("utf-8", errors="replace")


def _transfer_decode(part: BodyPartReader, data: Union[bytes, bytearray]) -> Union[bytes, bytearray]:
    transfer_encoding = part.headers.get(hdrs.CONTENT_TRANSFER_ENCODING, "").lower()
    if transfer_encoding == "base64":
        return base64.b64decode(data)
    if transfer_encoding == "quoted-printable":
        return binascii.a2b_qp(data)
    return data


def _content_decode(part: BodyPartReader, data: Union[bytes, bytearray]) -> Union[bytes, bytearray]:
    content_encoding = part.headers.get(hdrs.CONTENT_ENCODING, "").lower()
    needs_content_decoding = getattr(part, "_needs_content_decoding", None)
    if needs_content_decoding is not None:
        decodes = needs_content_decoding()
    else:
        decodes = not getattr(part, "_is_form_data", False)
    if not decodes or content_encoding not in ("deflate", "gzip"):
        return data
    wbits = 16 + zlib.MAX_WBITS if content_encoding == "gzip" else -zlib.MAX_WBITS
    return zlib.decompressobj(wbits).decompress(data, _MAX_DECODED_SIZE)
