"""Provider-specific credentials and JSON planning options, shared with smoke checks."""
import os
from urllib.parse import urlsplit


def model_key_name(base=None):
    base=os.getenv('MODEL_BASE_URL','') if base is None else base
    return 'GROQ_API_KEY' if urlsplit(base).hostname=='api.groq.com' else 'MODEL_API_KEY'


def model_api_key(base=None):
    # Never forward an existing GLM key to Groq when changing the base URL.
    return os.getenv(model_key_name(base),'').strip()


def model_options(base,model):
    name=model.lower();host=urlsplit(base).hostname
    if host=='api.groq.com' and name in ('openai/gpt-oss-20b','openai/gpt-oss-120b'):
        return {'reasoning_effort':'low','include_reasoning':False}
    options={'reasoning_effort':'none'} if host=='aihubmix.com' else {}
    if name in ('coding-glm-5.1-free','glm-5.1') or (host in ('api.z.ai','open.bigmodel.cn') and (name.startswith('glm-5') or 'flash' in name)):
        options['thinking']={'type':'disabled'}
    return options
