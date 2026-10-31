import os

from langchain_core.tools import tool
from main.core.model_provider import create_deepseek_llm
from langgraph.prebuilt import create_react_agent


@tool
def add_numbers(a: int, b: int) -> int:
    """Add two integers together."""
    return a + b


llm = create_deepseek_llm(temperature=0)


agent = create_react_agent(
    llm,
    [add_numbers]
)

result = agent.invoke({
    "messages": [
        ("user", "Use the add_numbers tool to calculate 17 + 25.")
    ]
})

for message in result["messages"]:
    print(type(message).__name__)
    print(message)
    print("-" * 60)
