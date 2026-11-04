import pytest
from backend.model_config import model_options,model_api_key,model_key_name

@pytest.mark.parametrize('base,model,expected',[
 ('https://api.groq.com/openai/v1','openai/gpt-oss-20b',{'reasoning_effort':'low','include_reasoning':False}),
 ('https://api.groq.com/openai/v1','openai/gpt-oss-120b',{'reasoning_effort':'low','include_reasoning':False}),
 ('https://api.groq.com.invalid/v1','openai/gpt-oss-20b',{}),
 ('https://api.groq.com/openai/v1','some-other-model',{}),
])
def test_groq_options_match_exact_host_and_model(base,model,expected):
 assert model_options(base,model)==expected
 assert 'reasoning_format' not in model_options(base,model)
 assert 'thinking' not in model_options(base,model)

def test_groq_cannot_reuse_existing_glm_key(monkeypatch):
 monkeypatch.setenv('MODEL_BASE_URL','https://api.groq.com/openai/v1')
 monkeypatch.setenv('MODEL_API_KEY','old-glm-key');monkeypatch.delenv('GROQ_API_KEY',raising=False)
 assert model_key_name()=='GROQ_API_KEY' and model_api_key()==''
 monkeypatch.setenv('GROQ_API_KEY','groq-test-key');assert model_api_key()=='groq-test-key'
 monkeypatch.setenv('MODEL_BASE_URL','https://open.bigmodel.cn/api/paas/v4');assert model_api_key()=='old-glm-key'
