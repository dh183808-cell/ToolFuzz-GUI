import os
from main.core.model_provider import create_deepseek_llm

api_key = os.getenv("DEEPSEEK_API_KEY")

if not api_key:
    raise RuntimeError("DEEPSEEK_API_KEY is not set")

llm = create_deepseek_llm(temperature=0.4)


response = llm.invoke("Reply with exactly: DeepSeek connection OK")

print(response.content)
