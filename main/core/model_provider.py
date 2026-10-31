import os

from langchain_openai import ChatOpenAI


DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"


def create_deepseek_llm(
    model_name=DEFAULT_MODEL,
    temperature=0,
):
    api_key = os.getenv("DEEPSEEK_API_KEY")

    if not api_key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY is not set."
        )

    return ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=DEFAULT_BASE_URL,
        temperature=temperature,
    )
