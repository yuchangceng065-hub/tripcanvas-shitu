#!/usr/bin/env python3
"""One-shot provider check for the JSON planner adapter.

Reads MODEL_BASE_URL / MODEL_API_KEY / MODEL_NAME (optionally from .env),
sends one small chat/completions request and verifies the JSON object.

    .venv/bin/python scripts/model-check.py

Never prints the API key. Exit codes: 0 ok, 2 missing config, 3 request or JSON failure.
"""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path = ROOT / '.env') -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def options_for(base: str, model: str) -> dict:
    name = model.lower()
    options: dict = {'reasoning_effort': 'none'} if base.startswith('https://aihubmix.com') else {}
    if name in ('coding-glm-5.1-free', 'glm-5.1') or (base.startswith('https://api.z.ai') and (name.startswith('glm-5') or 'flash' in name)):
        options['thinking'] = {'type': 'disabled'}
    return options


def main() -> int:
    load_env()
    base = os.getenv('MODEL_BASE_URL', '').strip()
    key = os.getenv('MODEL_API_KEY', '').strip()
    model = os.getenv('MODEL_NAME', '').strip()
    if not (base and key and model):
        print('缺少 MODEL_BASE_URL / MODEL_API_KEY / MODEL_NAME；请在 .env 或环境变量中配置。')
        return 2
    payload = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': '只输出一个 JSON 对象，不要 Markdown。'},
            {'role': 'user', 'content': '返回 {"ok": true, "city": "杭州", "days": 1}'},
        ],
        'response_format': {'type': 'json_object'},
        'temperature': 0,
        'max_tokens': 200,
        'stream': False,
        **options_for(base, model),
    }
    started = time.time()
    try:
        with httpx.Client(timeout=60) as client:
            response = client.post(base.rstrip('/') + '/chat/completions', headers={'Authorization': 'Bearer ' + key}, json=payload)
        elapsed = time.time() - started
        print(f'HTTP {response.status_code} · {elapsed:.1f}s · model={model} · provider={base}')
        if response.status_code != 200:
            print('供应商返回失败，未打印密钥。响应片段：', response.text[:300])
            return 3
        content = response.json()['choices'][0]['message']['content']
        value = json.loads(content)
        if not isinstance(value, dict):
            raise ValueError('返回内容不是 JSON 对象')
        print('JSON 校验通过，字段：', ', '.join(value.keys()))
        return 0
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as error:
        print('检查失败：', type(error).__name__, str(error)[:200])
        return 3


if __name__ == '__main__':
    sys.exit(main())
