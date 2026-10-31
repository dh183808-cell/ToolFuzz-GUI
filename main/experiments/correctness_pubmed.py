from langchain_community.tools.pubmed.tool import PubmedQueryRun

from main.core.correctness_runner import run_correctness


tool = PubmedQueryRun()


def main():
    return run_correctness(
        tool=tool,
        prompt_set_iters=1,
        prompt_temperature=0.4,
        agent_temperature=0,
        judge_temperature=0,
        output_name="correctness_pubmed_minimal",
        title="ToolFuzz Correctness - PubMed",
    )


if __name__ == "__main__":
    main()
