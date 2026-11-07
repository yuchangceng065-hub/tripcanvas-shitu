"""Model usage governance: billing semantics and immutable usage snapshots.

Token counts are observations, not invoices. A missing or partial provider
`usage` object is reported as `cost=None` (unknown), never as zero. A price
table that has not been configured is also unknown, never "free": free tiers
must be declared explicitly as zero via PRICE_*_PER_MILLION=0.
"""
from __future__ import annotations

import os
import time

SCHEMA_VERSION = 'usage-report/v1'
DEFAULT_PRICE_TABLE_VERSION = 'configurable-v1'


def _number(metrics, key, minimum=0.0):
    value = (metrics or {}).get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return minimum
    return value if value >= minimum else minimum


def price_table():
    """USD per 1M tokens. Unset prices return None (unknown), not zero."""
    def amount(name):
        raw = os.getenv(name)
        if raw is None:
            return None
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return None
    return {
        'promptPerMillion': amount('PRICE_PROMPT_PER_MILLION'),
        'completionPerMillion': amount('PRICE_COMPLETION_PER_MILLION'),
        'version': os.getenv('PRICE_TABLE_VERSION', DEFAULT_PRICE_TABLE_VERSION),
        'currency': os.getenv('PRICE_CURRENCY', 'USD'),
    }


def usage_report(metrics: dict | None, price: dict | None = None) -> dict:
    """Cost report. `costKnown=False` for unknown usage, unconfigured prices,
    or a total without a prompt/completion breakdown."""
    table = price or price_table()
    counts = {
        'promptTokens': _number(metrics, 'promptTokens'),
        'completionTokens': _number(metrics, 'completionTokens'),
        'totalTokens': _number(metrics, 'totalTokens'),
        'providerRequests': _number(metrics, 'providerRequests'),
        'providerSeconds': round(_number(metrics, 'providerSeconds'), 3),
    }
    common = {
        **counts,
        'currency': table['currency'],
        'priceTableVersion': table['version'],
        'schemaVersion': SCHEMA_VERSION,
    }

    def unknown(reason):
        return {**common, 'cost': None, 'costKnown': False, 'reason': reason}

    if not (bool(metrics) and metrics.get('tokensComplete') is True):
        return unknown('provider_usage_unknown')
    if table['promptPerMillion'] is None or table['completionPerMillion'] is None:
        return unknown('price_not_configured')
    has_breakdown = ('promptTokens' in metrics and 'completionTokens' in metrics)
    if counts['totalTokens'] > 0 and not has_breakdown:
        return unknown('usage_breakdown_unavailable')
    cost = (counts['promptTokens'] * table['promptPerMillion']
            + counts['completionTokens'] * table['completionPerMillion']) / 1_000_000
    return {**common, 'cost': round(cost, 6), 'costKnown': True, 'reason': 'ok'}


def usage_snapshot(metrics: dict | None, *, run_id: str, owner_id: str | None = None,
                   model: str | None = None, price: dict | None = None) -> dict:
    """Immutable record suitable for `run_events.detail` or a usage table."""
    return {
        'schemaVersion': SCHEMA_VERSION,
        'runId': run_id,
        'ownerId': owner_id,
        'model': model,
        'capturedAt': int(time.time()),
        'report': usage_report(metrics, price=price),
    }
