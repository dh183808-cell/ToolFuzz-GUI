from langchain_core.tools import tool

from main.core.runtime_runner import run_runtime


@tool
def fragile_lookup(query: str) -> str:
    """
    Look up an item using the supplied query text.
    """

    # Deliberately introduce a runtime bug for reproduction.
    if len(query) == 0:
        raise ValueError("query must not be empty")

    return f"Lookup result for: {query}"


def main():

    return run_runtime(
        tool=fragile_lookup,
        fuzzer_iters=1,
        prompt_temperature=0.4,
        agent_temperature=0,
        output_name="runtime_minimal",
        title="ToolFuzz Runtime Minimal Reproduction",
    )


if __name__ == "__main__":
    main()
