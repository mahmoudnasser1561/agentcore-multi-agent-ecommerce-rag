"""
agent_observability.py
=======================
CloudWatch logging + X-Ray tracing for the NovaMart multi-agent system.

- `tool` wraps strands.tool so every tool call is logged at INFO with its
  duration — the Inventory/Refund/Policy tool calls that show up as
  worker-agent nodes on the X-Ray Service Map.
- `tracer.trace_request(...)` publishes one X-Ray trace segment per customer
  request (via PutTraceSegments), independent of whether the process is
  running locally or inside AgentCore Runtime.
- `apply_observability_config` points a deployed runtime's logs and traces
  at the project's CloudWatch log group.
"""

import functools
import logging
import socket
import time
import uuid
from contextlib import contextmanager
from datetime import datetime

import boto3
from strands import tool as _strands_tool

import config

logger = logging.getLogger("novamart")
_xray = boto3.client("xray", region_name=config.AWS_REGION)
_logs = boto3.client("logs", region_name=config.AWS_REGION)
_agentcore_control = boto3.client("bedrock-agentcore-control", region_name=config.AWS_REGION)

_cw_handler = None  # installed by setup_logging(to_cloudwatch=True)


def tool(fn):
    """Drop-in replacement for @strands.tool that logs name + duration at INFO."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        start = time.monotonic()
        try:
            return fn(*args, **kwargs)
        finally:
            logger.info("tool=%s duration_ms=%.0f", fn.__name__, (time.monotonic() - start) * 1000)

    return _strands_tool(wrapper)


class _CloudWatchHandler(logging.Handler):
    """Batches log records and ships them to the project's CloudWatch log group.
    Used for local `test`/`chat` runs so they show up alongside the deployed
    runtime's own logs, which AgentCore ships automatically."""

    def __init__(self, log_group: str, stream: str):
        super().__init__()
        self.log_group = log_group
        self.stream = stream
        self._buffer: list[dict] = []
        self._ready = False
        try:
            _logs.create_log_group(logGroupName=log_group)
        except _logs.exceptions.ResourceAlreadyExistsException:
            pass
        try:
            _logs.create_log_stream(logGroupName=log_group, logStreamName=stream)
        except _logs.exceptions.ResourceAlreadyExistsException:
            pass
        self._ready = True

    def emit(self, record: logging.LogRecord) -> None:
        if not self._ready:
            return
        self._buffer.append({
            "timestamp": int(record.created * 1000),
            "message": self.format(record),
        })

    def flush(self) -> None:
        if not self._buffer:
            return
        try:
            _logs.put_log_events(
                logGroupName=self.log_group, logStreamName=self.stream,
                logEvents=sorted(self._buffer, key=lambda e: e["timestamp"]),
            )
        except Exception as exc:  # best-effort - a log outage must not stop the agent
            print(f"  [Note] CloudWatch log flush failed: {exc}")
        self._buffer.clear()


def setup_logging(to_cloudwatch: bool = False, level: int = logging.INFO) -> None:
    global _cw_handler
    logging.basicConfig(level=level, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if not to_cloudwatch:
        return
    try:
        stream = f"local/{socket.gethostname()}/{datetime.utcnow():%Y-%m-%d}"
        _cw_handler = _CloudWatchHandler(config.AGENT_LOG_GROUP, stream)
        logging.getLogger().addHandler(_cw_handler)
        logger.info("latest agent log stream: %s", stream)
    except Exception as exc:
        print(f"  [Note] CloudWatch logging not enabled: {exc}")


def flush_logs() -> None:
    if _cw_handler is not None:
        _cw_handler.flush()


class _Tracer:
    """One X-Ray trace segment per customer request."""

    last_trace_id: str | None = None
    last_published: bool = False

    @contextmanager
    def trace_request(self, session_id: str, customer_id: str, query: str):
        trace_id = f"1-{int(time.time()):08x}-{uuid.uuid4().hex[:24]}"
        segment_id = uuid.uuid4().hex[:16]
        start = time.time()
        self.last_trace_id = trace_id
        self.last_published = False
        try:
            yield trace_id
        finally:
            segment = {
                "name": "NovaMart-Orchestrator",
                "id": segment_id,
                "trace_id": trace_id,
                "start_time": start,
                "end_time": time.time(),
                "annotations": {"session_id": session_id, "customer_id": customer_id},
                "metadata": {"query": query},
            }
            try:
                import json
                _xray.put_trace_segments(TraceSegmentDocuments=[json.dumps(segment)])
                self.last_published = True
            except Exception as exc:
                print(f"  [Note] X-Ray segment not published: {exc}")


tracer = _Tracer()


def print_trace_hint() -> None:
    if tracer.last_trace_id:
        region = config.AWS_REGION
        print(f"  View trace: https://{region}.console.aws.amazon.com/cloudwatch/home"
              f"?region={region}#xray:traces/{tracer.last_trace_id}")


def apply_observability_config(runtime_arn: str, logging_configuration: dict) -> dict:
    """Point a deployed runtime's logs and traces at this project's resources."""
    _agentcore_control.update_agent_runtime(
        agentRuntimeArn=runtime_arn, loggingConfiguration=logging_configuration,
    )
    xray_cfg = logging_configuration.get("xRayConfig", {})
    return {
        "log_group": logging_configuration.get("cloudWatchConfig", {}).get("logGroupName"),
        "xray": {
            "destination": "CloudWatch Transaction Search",
            "indexing_percent": round(xray_cfg.get("samplingRate", 0) * 100),
        },
    }


def wait_for_runtime_ready(agentcore_control, runtime_id: str, timeout_s: int = 300) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = agentcore_control.get_agent_runtime(agentRuntimeId=runtime_id)["status"]
        if status == "READY":
            return
        if status == "FAILED":
            raise RuntimeError(f"Runtime {runtime_id} entered status FAILED")
        print(".", end="", flush=True)
        time.sleep(10)
    raise TimeoutError(f"Runtime {runtime_id} not READY after {timeout_s}s")
