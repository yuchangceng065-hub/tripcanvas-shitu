"""AG-UI semantic adapter for the existing SSE run stream.

This is a semantic VIEW, not a complete AG-UI implementation: it maps internal
phase names onto AG-UI event types and keeps the SSE cursor. It does not
implement the full AG-UI run lifecycle, message streams or client state model.
"""
from __future__ import annotations

import json

PROTOCOL = 'ag-ui/1.0'

PHASE_MAP = {
    'started': 'RUN_STARTED',
    'resumed': 'RUN_STARTED',
    'requirements': 'STEP_STARTED',
    'requirements_completed': 'STEP_FINISHED',
    'model': 'STEP_STARTED',
    'model_started': 'STEP_STARTED',
    'model_error': 'RUN_ERROR',
    'search': 'TOOL_CALL_START',
    'search_refine': 'TOOL_CALL_START',
    'mcp_call': 'TOOL_CALL_START',
    'tool_call': 'TOOL_CALL_START',
    'tool_result': 'TOOL_CALL_RESULT',
    'validation': 'STEP_FINISHED',
    'candidate_added': 'STATE_DELTA',
    'graph_pause': 'STATE_SNAPSHOT',
    'graph_resume': 'STATE_DELTA',
    'completed': 'RUN_FINISHED',
    'failed': 'RUN_ERROR',
    'interrupted': 'RUN_ERROR',
    'confirmed': 'STATE_DELTA',
}
FALLBACK = 'STATE_DELTA'

SENSITIVE_KEYS = frozenset({'key', 'token', 'secret', 'authorization', 'password',
                            'prompt', 'cookie', 'credential'})
# Telemetry fields are NOT secrets: they feed the frozen billing snapshot and
# the AG-UI cost view. Keep them even though their names contain "token"/"prompt".
USAGE_KEYS = frozenset({'usage', 'model_usage', 'prompt_tokens', 'completion_tokens',
                        'total_tokens', 'tokens_complete', 'provider_requests',
                        'repair_requests', 'provider_seconds'})
MAX_DEPTH = 3
MAX_ITEMS = 24
MAX_STRING = 200


def _sensitive(key) -> bool:
    """Match credential-like key NAMES, not substrings of telemetry fields.

    'apiKey' / 'accessToken' are secrets; 'promptTokens' / 'tokensComplete'
    are usage counters and must survive scrubbing. Splitting CamelCase and
    separators into words lets us tell them apart.
    """
    raw = str(key)
    normalized = ''.join(('_' + char.lower()) if char.isupper() else char
                         for char in raw).lower()
    if normalized in USAGE_KEYS:
        return False
    words = {word for word in ''.join(
        char if char.isalnum() else '_' for char in normalized).split('_') if word}
    return bool(words & SENSITIVE_KEYS)


def _clean(value, depth: int = 0):
    """Recursive scrubbing: sensitive keys are filtered at EVERY nesting level."""
    # Scalars are always safe to keep; depth limits only nested containers.
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_STRING]
    if depth >= MAX_DEPTH:
        return '…'
    if isinstance(value, (list, tuple)):
        return [_clean(item, depth + 1) for item in list(value)[:MAX_ITEMS]]
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:MAX_ITEMS]:
            if _sensitive(key):
                continue
            result[str(key)[:64]] = _clean(item, depth + 1)
        return result
    return str(value)[:MAX_STRING]


def _safe_detail(detail: dict | None) -> dict:
    cleaned = _clean(detail or {})
    return cleaned if isinstance(cleaned, dict) else {'value': cleaned}


def to_agui_event(phase: str, status: str, detail: dict | None, seq: int,
                  run_id: str, thread_id: str | None = None) -> dict:
    detail = detail or {}
    event_type = PHASE_MAP.get(phase, FALLBACK)
    event = {
        'type': event_type,
        'protocol': PROTOCOL,
        'threadId': thread_id or run_id,
        'runId': run_id,
        'seq': seq,
        'rawPhase': phase,
        'rawStatus': status,
        'detail': _safe_detail(detail),
    }
    if event_type.startswith('TOOL_CALL'):
        # A tool call and its result share `step`, so a client can correlate
        # them. Falling back to seq would give them different ids.
        step = detail.get('step')
        event['toolCallId'] = f'{run_id}:step:{step}' if step is not None else f'{run_id}:{seq}'
        event['toolCallName'] = str(detail.get('tool') or phase)
        args = detail.get('args')
        event['args'] = _safe_detail(args) if isinstance(args, dict) else {}
        if event_type == 'TOOL_CALL_RESULT':
            event['result'] = _clean({key: detail[key] for key in
                                      ('ok', 'summary', 'error', 'step') if key in detail})
    if event_type == 'STATE_DELTA':
        event['delta'] = [{'op': 'replace', 'path': '/run/phase', 'value': phase}]
    if event_type == 'STATE_SNAPSHOT':
        event['snapshot'] = {'runId': run_id, 'phase': phase, 'status': status}
    if event_type == 'RUN_ERROR':
        event['message'] = str(detail.get('error') or status)[:MAX_STRING]
    return event


def to_agui_frame(phase: str, status: str, detail: dict | None, seq: int,
                  run_id: str, thread_id: str | None = None) -> str:
    """One SSE frame; `id` keeps the same monotonic cursor as the native stream."""
    event = to_agui_event(phase, status, detail, seq, run_id, thread_id)
    data = json.dumps(event, ensure_ascii=False, separators=(',', ':'))
    return f'id: {seq}\nevent: {event["type"]}\ndata: {data}\n\n'


def capabilities() -> dict:
    return {
        'protocol': PROTOCOL,
        'compatibility': 'semantic-view',
        'transport': 'text/event-stream',
        'cursor': 'Last-Event-ID over monotonic seq',
        'nativeFormat': 'sse',
        'adapterFormat': 'ag-ui',
        'toolCallCorrelation': 'runId:step:<step> (falls back to seq)',
        'mappedPhases': sorted(PHASE_MAP),
        'unmappedFallback': FALLBACK,
        'note': 'Event-type mapping only; not a full AG-UI run/message lifecycle.',
    }
