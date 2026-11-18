from main.compat.correctness import CompatibleCorrectnessTester
from main.compat.react_agent_new import CompatibleReactAgentNew
from main.core.model_provider import (
    DEFAULT_MODEL,
    create_deepseek_llm,
)
from main.core.run_result import (
    build_run_result,
    start_run,
)

from main.core.run_reports import save_reports
from main.core.run_store import (
    DEFAULT_RESULTS_DIR, checked_directory, persist_run, redact_credentials,
    report_path, task_directory,
)


def _serialize_result(result):
    """
    Convert ToolFuzz/Pydantic result objects into ordinary dictionaries
    for later GUI usage.
    """

    if hasattr(result, "model_dump"):
        return result.model_dump()

    if hasattr(result, "dict"):
        return result.dict()

    return result


def run_correctness(
    tool,
    *,
    model_name=DEFAULT_MODEL,
    prompt_temperature=0.4,
    agent_temperature=0,
    judge_temperature=0,
    prompt_set_iters=1,
    output_name=None,
    results_dir=None,
    task_id=None,
    persist_result=True,
    run_context=None,
    clear_previous=True,
    title="ToolFuzz Correctness Test",
):
    """
    Run one ToolFuzz Correctness test.

    This function is the stable backend entry point used by
    experiments now and by the GUI later.
    """

    run_context = run_context or start_run()
    task_id = task_id or run_context.run_id
    results_dir = (
        task_directory(task_id) if results_dir is None
        else checked_directory(results_dir)
    )
    output_path = report_path(results_dir, output_name or "correctness")

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

    judge_llm = create_deepseek_llm(
        model_name=model_name,
        temperature=judge_temperature,
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

    tester = CompatibleCorrectnessTester(
        llm=judge_llm,
        tool=tool,
        agent=agent,
        prompt_set_iters=prompt_set_iters,
        prompt_model=prompt_llm,
        judge_model=judge_llm,
    )

    tool_name = getattr(
        tool,
        "name",
        tool.__class__.__name__,
    )

    print("\n==========================================")
    print(f" {title}")
    print("==========================================")
    print(f"Model : {model_name}")
    print(f"Tool  : {tool_name}")
    print(f"Prompt sets: {prompt_set_iters}")
    print("==========================================\n")

    # ---------------------------------------------------------
    # 4. Run
    # ---------------------------------------------------------

    tester.test()

    # ---------------------------------------------------------
    # 5. Save
    # ---------------------------------------------------------

    report_error = None
    try:
        save_reports(
            tester.results, output_path, clear_previous=clear_previous,
        )
    except Exception as exc:
        # A completed test remains completed even if its reports cannot be saved.
        report_error = redact_credentials(f"{type(exc).__name__}: {exc}")

    # ---------------------------------------------------------
    # 6. Normalize result for experiment / GUI usage
    # ---------------------------------------------------------

    results = [
        _serialize_result(result)
        for result in tester.results
    ]
    individual_results = [
        item
        for prompt_set in results
        for item in prompt_set["individual_run_test_results"]
    ]
    tool_failures = sum(item["tool_failure"] for item in individual_results)

    print("\n==========================================")
    print("Finished")
    print(f"Prompt sets: {len(results)}")
    print(f"JSON : {output_path}.json")
    print(f"HTML : {output_path}.html")
    print("==========================================")

    result = build_run_result(
        task_id=task_id,
        context=run_context,
        status="completed",
        test_type="correctness",
        tool=tool_name,
        model=model_name,
        config={
            "prompt_set_iters": prompt_set_iters,
            "prompt_temperature": prompt_temperature,
            "agent_temperature": agent_temperature,
            "judge_temperature": judge_temperature,
            "clear_previous": clear_previous,
        },
        summary={
            "prompt_set_count": len(results),
            "prompt_count": len(individual_results),
            "tool_failure_count": tool_failures,
        },
        results=results,
        json_path=str(output_path) + ".json",
        html_path=str(output_path) + ".html",
    )

    if report_error is not None:
        result["artifacts"]["save_error"] = report_error

    if persist_result:
        return persist_run(result, task_id=task_id, directory=results_dir)
    return result
