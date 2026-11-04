"""Request-local counters only. Never store prompts, credentials or reasoning."""
from contextvars import ContextVar

current_metrics=ContextVar('model_metrics',default=None)

def empty_metrics():
    return {'providerRequests':0,'repairRequests':0,'providerSeconds':0.0,
        'promptTokens':0,'completionTokens':0,'totalTokens':0,'tokensComplete':True}

def record_usage(metrics,usage):
    if metrics is None:return
    for source,target in (('prompt_tokens','promptTokens'),('completion_tokens','completionTokens'),('total_tokens','totalTokens')):
        value=usage.get(source) if isinstance(usage,dict) else None
        if isinstance(value,int) and not isinstance(value,bool) and value>=0:metrics[target]+=value
        else:metrics['tokensComplete']=False
