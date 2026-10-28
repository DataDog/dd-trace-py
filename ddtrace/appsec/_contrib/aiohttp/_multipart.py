import asyncio
import base64
import binascii
from typing import Any
from typing import Optional
from typing import Union
import zlib

from aiohttp import hdrs
from aiohttp.multipart import BodyPartReader
from aiohttp.multipart import MultipartReader
from aiohttp.multipart import content_disposition_filename
from aiohttp.multipart import parse_content_disposition
from aiohttp.web import Request

from ddtrace.appsec._contrib.aiohttp._utils import decode_text
from ddtrace.appsec._contrib.aiohttp._utils import parse_form
from ddtrace.appsec._contrib.aiohttp._utils import single_or_list
from ddtrace.appsec._ddwaf.ddwaf_types import DDWAF_MAX_CONTAINER_DEPTH
from ddtrace.contrib.internal.aiohttp.middlewares import DEFAULT_CLIENT_MAX_SIZE
from ddtrace.contrib.internal.aiohttp.middlewares import _restore_request_body
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.http import MediaType
from ddtrace.internal.utils.http import classify_media_type
from ddtrace.internal.utils.http import inspectable_charset
from ddtrace.internal.utils.http import is_punycode_charset
from ddtrace.internal.utils.http import is_slow_charset
from ddtrace.internal.utils.http import is_utf8_charset
from ddtrace.internal.utils.http import parse_json_bytes


logger = get_logger(__name__)

_MAX_DEPTH = DDWAF_MAX_CONTAINER_DEPTH
_MAX_PART_DECOMPRESSED = 16 * 1024
_MAX_TOTAL_DECOMPRESSED = 4 * 1024 * 1024
_DEFAULT_MAX_PARTS = 1000
_YIELD_EVERY = 25
_MAX_SLOW_CHARSET_DECODED = 16 * 1024
_MAX_JSON_PARSED = 1024 * 1024
_MAX_COMPRESSED_MEMBERS = 256


class _Walk:
    __slots__ = (
        "decompress_budget",
        "slow_charset_budget",
        "json_budget",
        "members_left",
        "exhausted",
        "parts_left",
        "max_size",
    )

    def __init__(self, max_fields: int, max_size: int) -> None:
        self.decompress_budget = _MAX_TOTAL_DECOMPRESSED
        self.slow_charset_budget = _MAX_SLOW_CHARSET_DECODED
        self.json_budget = _MAX_JSON_PARSED
        self.members_left = _MAX_COMPRESSED_MEMBERS
        self.exhausted = False
        self.parts_left = max_fields
        self.max_size = max_size


async def multipart_fields(request: Request, raw_body: bytes) -> dict[str, Any]:
    _restore_request_body(request, raw_body)
    max_fields = getattr(request, "_client_max_fields", 0)
    walk = _Walk(
        max_fields if max_fields > 0 else _DEFAULT_MAX_PARTS,
        getattr(request, "_client_max_size", 0) or DEFAULT_CLIENT_MAX_SIZE,
    )
    return await _read_fields(await request.multipart(), walk, 0)


async def _read_fields(reader: MultipartReader, walk: _Walk, depth: int) -> dict[str, Any]:
    fields: dict[str, list[Any]] = {}
    while not walk.exhausted:
        part = await _next_part(reader, walk)
        if part is None:
            break
        name, filename = _disposition(part)
        if isinstance(part, MultipartReader):
            if depth < _MAX_DEPTH:
                fields.setdefault(name, []).append(await _read_fields(part, walk, depth + 1))
            else:
                await _drain(part, walk)
            continue
        data = await _read_part(part, walk, collect=not filename)
        if not filename:
            try:
                values = _decode(part, data, walk)
            except Exception:
                logger.debug("aiohttp::multipart part decoding failed", exc_info=True)
                values = [decode_text(data, "utf-8")]
            fields.setdefault(name, []).extend(values)
    return single_or_list(fields)


async def _next_part(reader: MultipartReader, walk: _Walk) -> Optional[Union[MultipartReader, BodyPartReader]]:
    try:
        part = await reader.next()
    except Exception:
        logger.debug("aiohttp::multipart walk stopped", exc_info=True)
        # Stop everything: the parent's next() would otherwise run aiohttp's unguarded release().
        walk.exhausted = True
        return None
    if part is None:
        return None
    walk.parts_left -= 1
    if walk.parts_left < 0:
        walk.exhausted = True
        return None
    if walk.parts_left % _YIELD_EVERY == 0:
        await asyncio.sleep(0)
    return part


async def _drain(reader: MultipartReader, walk: _Walk) -> None:
    readers = [reader]
    while readers and not walk.exhausted:
        part = await _next_part(readers[-1], walk)
        if part is None:
            readers.pop()
        elif isinstance(part, MultipartReader):
            readers.append(part)
        else:
            await _read_part(part, walk, collect=False)


async def _read_part(part: BodyPartReader, walk: _Walk, collect: bool) -> bytearray:
    data = bytearray()
    try:
        while not part.at_eof():
            chunk = await part.read_chunk()
            # An empty chunk before the part ends means the stream ran out (CVE-2024-30251 loops here).
            if not chunk and not part.at_eof():
                walk.exhausted = True
                break
            if collect:
                data.extend(chunk)
    except Exception:
        logger.debug("aiohttp::multipart part read failed", exc_info=True)
        walk.exhausted = True
    return data


def _disposition(part: Union[MultipartReader, BodyPartReader]) -> tuple[str, str]:
    try:
        _, params = parse_content_disposition(part.headers.get(hdrs.CONTENT_DISPOSITION))
    except Exception:
        return "", ""
    try:
        filename = content_disposition_filename(params, "filename") or ""
    except Exception:
        filename = ""
    try:
        name = content_disposition_filename(params, "name") or ""
    except Exception:
        name = ""
    return name, filename


def _decode(part: BodyPartReader, raw: bytearray, walk: _Walk) -> list[Any]:
    content_type = part.headers.get(hdrs.CONTENT_TYPE)
    media_type = classify_media_type(content_type)
    data: Union[bytes, bytearray] = raw
    try:
        data = _transfer_decode(part, data)
    except Exception:
        logger.debug("aiohttp::multipart transfer decoding failed", exc_info=True)
    transfer_decoded = data
    try:
        data = _content_decode(part, data, walk)
    except Exception:
        logger.debug("aiohttp::multipart content decoding failed", exc_info=True)
    declared_charset = part.get_charset(default="")
    punycode = is_punycode_charset(declared_charset)
    if declared_charset:
        declared_charset = inspectable_charset(declared_charset, len(data))
        if is_slow_charset(declared_charset):
            if walk.slow_charset_budget < len(data):
                declared_charset = "utf-8"
            else:
                walk.slow_charset_budget -= len(data)
    decompressed = data is not transfer_decoded
    if decompressed and media_type is MediaType.JSON:
        if walk.json_budget < len(data):
            media_type = MediaType.PLAIN
        else:
            walk.json_budget -= len(data)
    is_text = content_type is None or content_type.startswith("text/")
    charset = (declared_charset or "utf-8") if is_text else "utf-8"
    values = [_value(data, charset, media_type, punycode)]
    if not is_text and declared_charset and not is_utf8_charset(declared_charset):
        values.append(_value(data, declared_charset, media_type, punycode))
    if punycode:
        values.append(decode_text(data, "utf-8"))
    if transfer_decoded is not raw or (not decompressed and not is_utf8_charset(charset)):
        values.append(decode_text(raw, "utf-8"))
    return [value for index, value in enumerate(values) if value not in values[:index]]


def _value(data: Union[bytes, bytearray], charset: str, media_type: MediaType, punycode: bool) -> Any:
    if media_type is MediaType.JSON:
        try:
            return parse_json_bytes(data, charset, punycode)
        except ValueError:
            logger.debug("aiohttp::multipart JSON parsing failed", exc_info=True)
    if media_type is MediaType.FORM_URLENCODED:
        return parse_form(data, charset)
    return decode_text(data, charset)


def _transfer_decode(part: BodyPartReader, data: Union[bytes, bytearray]) -> Union[bytes, bytearray]:
    transfer_encoding = part.headers.get(hdrs.CONTENT_TRANSFER_ENCODING, "").lower()
    if transfer_encoding == "base64":
        return base64.b64decode(data)
    if transfer_encoding == "quoted-printable":
        return binascii.a2b_qp(data)
    return data


def _content_decode(part: BodyPartReader, data: Union[bytes, bytearray], walk: _Walk) -> Union[bytes, bytearray]:
    content_encoding = part.headers.get(hdrs.CONTENT_ENCODING, "").lower()
    if content_encoding not in ("deflate", "gzip") or not _aiohttp_decodes(part):
        return data
    limit = max(_MAX_PART_DECOMPRESSED, min(walk.max_size, walk.decompress_budget))
    wbits = 16 + zlib.MAX_WBITS if content_encoding == "gzip" else -zlib.MAX_WBITS
    decompressed = bytearray()
    try:
        while data and len(decompressed) < limit and walk.members_left > 0:
            walk.members_left -= 1
            decompressor = zlib.decompressobj(wbits)
            decompressed += decompressor.decompress(data, limit - len(decompressed))
            data = decompressor.unused_data
            if not decompressor.eof or (content_encoding == "gzip" and not data.startswith(b"\x1f\x8b")):
                break
    except zlib.error:
        logger.debug("aiohttp::multipart decompression stopped", exc_info=True)
    finally:
        walk.decompress_budget -= len(decompressed)
    return decompressed if decompressed else data


def _aiohttp_decodes(part: BodyPartReader) -> bool:
    needs_content_decoding: Optional[Any] = getattr(part, "_needs_content_decoding", None)
    if needs_content_decoding is not None:
        return bool(needs_content_decoding())
    return not getattr(part, "_is_form_data", False)
