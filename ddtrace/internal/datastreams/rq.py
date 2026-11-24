from __future__ import annotations

from typing import Any
from typing import Optional

from ddtrace import config
from ddtrace.internal import core
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY_BASE_64
from ddtrace.internal.datastreams.processor import DsmPathwayCodec
from ddtrace.internal.datastreams.utils import _calculate_byte_size
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def _edge_tags(direction: str, queue_name: Optional[str]) -> list[str]:
    return [f"direction:{direction}", f"topic:{queue_name or ''}", "type:redis"]


def _payload_size(job: Any) -> int:
    return _calculate_byte_size(getattr(job, "args", None)) + _calculate_byte_size(getattr(job, "kwargs", None))


def _produce(queue: Any, job: Any, span: Optional[Any]) -> None:
    from . import data_streams_processor as processor

    p = processor()
    if p is None:
        return
    ctx = p.set_checkpoint(_edge_tags("out", queue.name), payload_size=_payload_size(job), span=span)
    carrier: dict[str, str] = {}
    DsmPathwayCodec.encode(ctx, carrier)
    if carrier:
        job.meta = {**(job.meta or {}), **carrier}


def dsm_rq_create_job(queue: Any, job: Any) -> None:
    _produce(queue, job, None)


def dsm_rq_enqueue_job(queue: Any, job: Any, span: Optional[Any]) -> None:
    if isinstance(job.meta, dict) and PROPAGATION_KEY_BASE_64 in job.meta:
        return
    _produce(queue, job, span)


def dsm_rq_perform_job(job: Any, span: Optional[Any]) -> None:
    from . import data_streams_processor as processor

    p = processor()
    if p is None:
        return
    meta = job.meta if isinstance(job.meta, dict) else {}
    try:
        ctx = DsmPathwayCodec.decode(meta, p)
    except Exception:
        log.debug("could not decode the DSM pathway of rq job %s", getattr(job, "id", None), exc_info=True)
        ctx = p.new_pathway()
    ctx.set_checkpoint(_edge_tags("in", job.origin), payload_size=_payload_size(job), span=span)


def dsm_rq_after_perform_job(ctx: Any) -> None:
    from . import _processor

    if _processor is not None:
        _processor.periodic()


if config._data_streams_enabled:
    core.on("rq.queue.create_job", dsm_rq_create_job)
    core.on("rq.queue.enqueue_job.dsm", dsm_rq_enqueue_job)
    core.on("rq.worker.perform_job.start", dsm_rq_perform_job)
    core.on("rq.worker.after.perform.job", dsm_rq_after_perform_job)
