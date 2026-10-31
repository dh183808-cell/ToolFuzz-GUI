import os

from langchain_core.tools import tool
from main.core.model_provider import create_deepseek_llm

from main.compat.react_agent_new import CompatibleReactAgentNew


@tool
def add_numbers(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


llm = create_deepseek_llm(temperature=0)


agent = CompatibleReactAgentNew(
    add_numbers,
    llm,
)


result = agent(
    "Use add_numbers to calculate 17 + 25."
)


print("Agent response:")
print(result.agent_response)

print("\nTool arguments:")
print(result.tool_args)

print("\nTool output:")
print(result.tool_output)

print("\nTool invoked:")
print(result.is_tool_invoked)

print("\nTrace:")
for item in result.trace:
    print(item)
