from pathlib import Path

from main.compat.react_agent_new import CompatibleReactAgentNew
from main.compat.runtime_tester import CompatibleRuntimeErrorTester
from main.core.model_provider import (
    DEFAULT_MODEL,
    create_deepseek_llm,
)
from main.core.run_result import (
    build_run_result,
    start_run,
)


MAIN_DIR = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS_DIR = MAIN_DIR / "results"


def _serialize_test_result(result):
    """
    Convert ToolFuzz/Pydantic result objects into ordinary dictionaries
    for later GUI usage.
    """

    if hasattr(result, "model_dump"):
        return result.model_dump()

    if hasattr(result, "dict"):
        return result.dict()

    return result


def run_runtime(
    tool,
    *,
    model_name=DEFAULT_MODEL,
    prompt_temperature=0,
    agent_temperature=0,
    fuzzer_iters=1,
    output_name=None,
    results_dir=None,
    clear_previous=True,
    title="ToolFuzz Runtime Test",
):
    """
    Run one ToolFuzz Runtime test.

    This function is the stable backend entry point used by
    experiments now and by the GUI later.
    """

    run_context = start_run()

    # ---------------------------------------------------------
    # 1. Models
    # ---------------------------------------------------------

    prompt_llm = create_deepseek_llm(
        model_name=model_name,
        temperature=prompt_temperature,
    )

    agent_llm = create_deepseek_llm(
        model_name=model_name,
        temperature=agent_temperature,
    )

    # ---------------------------------------------------------
    # 2. Agent
    # ---------------------------------------------------------

    agent = CompatibleReactAgentNew(
        tool,
        agent_llm,
    )

    if agent.agent is None:
        raise RuntimeError(
            "CompatibleReactAgentNew could not create "
            "the LangGraph agent."
        )

    # ---------------------------------------------------------
    # 3. Tester
    # ---------------------------------------------------------

    tester = CompatibleRuntimeErrorTester(
        llm=prompt_llm,
        tool=tool,
        agent=agent,
        fuzzer_iters=fuzzer_iters,
    )

    tool_name = getattr(
        tool,
        "name",
        tool.__class__.__name__,
    )

    print("\n====================================")
    print(f" {title}")
    print("====================================")
    print(f"Model: {model_name}")
    print(f"Tool : {tool_name}")
    print(f"Fuzzer iterations: {fuzzer_iters}")
    print("====================================\n")

    # ---------------------------------------------------------
    # 4. Run
    # ---------------------------------------------------------

    tester.test()

    # ---------------------------------------------------------
    # 5. Save
    # ---------------------------------------------------------

    if results_dir is None:
        results_dir = DEFAULT_RESULTS_DIR
    else:
        results_dir = Path(results_dir)

    results_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    if output_name is None:
        output_name = f"runtime_{tool_name}"

    output_path = results_dir / output_name

    if clear_previous:
        for suffix in (".json", ".html"):
            old_file = Path(
                str(output_path) + suffix
            )

            if old_file.exists():
                old_file.unlink()

    tester.save(str(output_path))

    # ---------------------------------------------------------
    # 6. Normalize result for experiment / GUI usage
    # ---------------------------------------------------------

    test_results = [
        _serialize_test_result(result)
        for result in tester.test_results
    ]

    successful_triggers = sum(
        1
        for result in test_results
        if isinstance(result, dict)
        and result.get("successful_trigger") is True
    )

    print("\n====================================")
    print("Finished.")
    print(
        f"Number of test results: "
        f"{len(test_results)}"
    )
    print(f"JSON : {output_path}.json")
    print(f"HTML : {output_path}.html")
    print("====================================")

    return build_run_result(
        context=run_context,
        status="completed",
        test_type="runtime",
        tool=tool_name,
        model=model_name,
        config={
            "fuzzer_iters": fuzzer_iters,
            "prompt_temperature": prompt_temperature,
            "agent_temperature": agent_temperature,
            "clear_previous": clear_previous,
        },
        summary={
            "test_count": len(test_results),
            "successful_triggers": successful_triggers,
        },
        results=test_results,
        json_path=str(output_path) + ".json",
        html_path=str(output_path) + ".html",
    )
