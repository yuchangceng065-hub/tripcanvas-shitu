"""Render/Railway supply PORT; single worker keeps public routing throttling shared."""
import os
import uvicorn

if __name__ == '__main__':
    uvicorn.run(
        'backend.app:app',
        host='0.0.0.0',
        port=int(os.environ.get('PORT', '8000')),
        workers=1,
        # Render terminates TLS and forwards the real client address. Trusting
        # the platform proxy keeps per-IP throttling meaningful; override with
        # FORWARDED_ALLOW_IPS when self-hosting behind a different proxy.
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get('FORWARDED_ALLOW_IPS', '*'),
    )
