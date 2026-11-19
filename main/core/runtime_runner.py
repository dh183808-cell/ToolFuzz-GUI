from main.compat.react_agent_new import CompatibleReactAgentNew
from main.compat.runtime_tester import CompatibleRuntimeErrorTester
from main.core.model_provider import (
    DEFAULT_MODEL,
    check_model_calls, create_role_models, resolve_roles,
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
    model_roles=None,
    prompt_temperature=0,
    agent_temperature=0,
    fuzzer_iters=1,
    output_name=None,
    results_dir=None,
    task_id=None,
    persist_result=True,
    run_context=None,
    clear_previous=True,
    title="ToolFuzz Runtime Test",
):
    """
    Run one ToolFuzz Runtime test.

    This function is the stable backend entry point used by
    experiments now and by the GUI later.
    """

    run_context = run_context or start_run()
    task_id = task_id or run_context.run_id
    results_dir = (
        task_directory(task_id) if results_dir is None
        else checked_directory(results_dir)
    )
    output_path = report_path(results_dir, output_name or "runtime")

    # ---------------------------------------------------------
    # 1. Models
    # ---------------------------------------------------------

    roles = resolve_roles(
        model_roles, model_name=model_name,
        prompt_temperature=prompt_temperature, agent_temperature=agent_temperature,
    )
    models = create_role_models(
        roles, correctness=False, legacy=model_roles is None,
    )
    prompt_llm, agent_llm = models["prompt"], models["agent"]
    model_name = roles.agent.model_id
    secret_env_names = [getattr(roles, name).api_key_env for name in models]

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
    # Compatibility testers may catch model exceptions; never publish their verdicts.
    check_model_calls(models)

    # ---------------------------------------------------------
    # 5. Save
    # ---------------------------------------------------------

    report_error = None
    try:
        save_reports(
            tester.test_results, output_path, clear_previous=clear_previous,
            secret_env_names=secret_env_names,
        )
    except Exception as exc:
        # A completed test remains completed even if its reports cannot be saved.
        report_error = redact_credentials(f"{type(exc).__name__}: {exc}", secret_env_names=secret_env_names)

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

    result = build_run_result(
        task_id=task_id,
        context=run_context,
        status="completed",
        test_type="runtime",
        tool=tool_name,
        model=model_name,
        config={
            "model_roles": roles.summary(),
            "fuzzer_iters": fuzzer_iters,
            "prompt_temperature": roles.prompt.temperature,
            "agent_temperature": roles.agent.temperature,
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

    if report_error is not None:
        result["artifacts"]["save_error"] = report_error

    result = redact_credentials(result, secret_env_names=secret_env_names)
    if persist_result:
        return persist_run(result, task_id=task_id, directory=results_dir)
    return result
