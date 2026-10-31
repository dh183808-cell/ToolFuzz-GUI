from langchain_community.tools import DuckDuckGoSearchRun

from main.core.runtime_runner import run_runtime


tool = DuckDuckGoSearchRun()


def main():

    return run_runtime(
        tool=tool,
        fuzzer_iters=3,
        prompt_temperature=0,
        agent_temperature=0,
        output_name="runtime_duckduckgo",
        title="ToolFuzz Runtime - DuckDuckGo",
    )


if __name__ == "__main__":
    main()
