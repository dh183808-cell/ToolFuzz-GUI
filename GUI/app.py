import streamlit as st

from GUI.services.task_manager import RunConfig, get_task_manager
from main.core.model_provider import DEFAULT_MODEL
from main.core.tool_registry import list_tool_options


def restore_config(task):
    """Restore a new session, and reject edits during a running test."""
    if task is None:
        return
    if (
        task.status != "running"
        and st.session_state.get("displayed_task_id") == task.task_id
    ):
        return

    config = task.config
    st.session_state.update(
        displayed_task_id=task.task_id,
        test_type=config.test_type,
        tool_id=config.tool_id,
        model_name=config.model_name,
        iterations=config.iterations,
        agent_temperature=float(config.agent_temperature),
        judge_temperature=float(config.judge_temperature),
    )
    st.session_state[f"{config.test_type.lower()}_prompt_temperature"] = float(
        config.prompt_temperature
    )


def start_test(manager, displayed_task_id):
    """Called before rendering, so running controls are disabled immediately."""
    st.session_state.start_error = None
    model_name = st.session_state.model_name.strip()
    if not model_name:
        st.session_state.start_error = "Model name must not be empty."
        return

    config = RunConfig(
        test_type=st.session_state.test_type,
        tool_id=st.session_state.tool_id,
        model_name=model_name,
        iterations=int(st.session_state.iterations),
        prompt_temperature=float(
            st.session_state[
                f"{st.session_state.test_type.lower()}_prompt_temperature"
            ]
        ),
        agent_temperature=float(st.session_state.agent_temperature),
        judge_temperature=float(st.session_state.get("judge_temperature", 0)),
    )
    manager.start(config, expected_task_id=displayed_task_id)


def render_result(result):
    st.subheader("Run Summary")
    metadata = result.get("metadata", {})
    summary = result.get("summary", {})
    artifacts = result.get("artifacts", {})

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Status", result.get("status", "-"))
    col2.metric("Test Type", result.get("test_type", "-"))
    col3.metric("Tool", result.get("tool", "-"))
    col4.metric("Duration", f"{metadata.get('duration_seconds', 0)} s")
    st.write("**Model:**", result.get("model", "-"))
    st.write("**Run ID:**", metadata.get("run_id", "-"))

    st.subheader("Summary")
    st.json(summary)
    st.subheader("Results")
    results = result.get("results", [])
    if results:
        for index, item in enumerate(results, start=1):
            with st.expander(f"Result {index}", expanded=False):
                st.json(item)
    else:
        st.info("No test results were produced.")

    st.subheader("Reports")
    st.write("**JSON:**", artifacts.get("json_path", "-"))
    st.write("**HTML:**", artifacts.get("html_path", "-"))


def main():
    st.set_page_config(page_title="ToolFuzz GUI", page_icon="🧪", layout="wide")
    st.title("ToolFuzz GUI")
    st.caption("Run ToolFuzz Runtime and Correctness tests.")

    manager = get_task_manager()
    task = manager.snapshot()
    running = task is not None and task.status == "running"
    for key, value in {
        "model_name": DEFAULT_MODEL,
        "iterations": 1,
        "runtime_prompt_temperature": 0.0,
        "correctness_prompt_temperature": 0.4,
        "agent_temperature": 0.0,
        "judge_temperature": 0.0,
    }.items():
        st.session_state.setdefault(key, value)
    restore_config(task)

    if task is not None:
        if st.query_params.get("task") != task.task_id:
            st.query_params["task"] = task.task_id
    elif st.query_params.get("task"):
        st.warning(
            "The previous test is no longer available. "
            "The Streamlit service may have restarted."
        )

    tool_labels = {option["id"]: option["name"] for option in list_tool_options()}
    with st.sidebar:
        st.header("Test Configuration")
        test_type = st.selectbox(
            "Test Type", ["Runtime", "Correctness"], key="test_type", disabled=running
        )
        st.selectbox(
            "Tool",
            options=list(tool_labels),
            format_func=tool_labels.get,
            key="tool_id",
            disabled=running,
        )
        st.text_input("Model", key="model_name", disabled=running)
        st.number_input(
            "Fuzzer Iterations" if test_type == "Runtime" else "Prompt Set Iterations",
            min_value=1,
            max_value=100,
            step=1,
            key="iterations",
            disabled=running,
        )
        st.number_input(
            "Prompt Temperature",
            min_value=0.0,
            max_value=2.0,
            step=0.1,
            key=f"{test_type.lower()}_prompt_temperature",
            disabled=running,
        )
        st.number_input(
            "Agent Temperature",
            min_value=0.0,
            max_value=2.0,
            step=0.1,
            key="agent_temperature",
            disabled=running,
        )
        if test_type == "Correctness":
            st.number_input(
                "Judge Temperature",
                min_value=0.0,
                max_value=2.0,
                step=0.1,
                key="judge_temperature",
                disabled=running,
            )

        st.button(
            "Start Test",
            key="start_test",
            type="primary",
            use_container_width=True,
            disabled=running,
            on_click=start_test,
            args=(manager, task.task_id if task is not None else None),
        )
        if st.session_state.get("start_error"):
            st.error(st.session_state.start_error)
        if running:
            st.info("A test is running. Configuration is locked until it finishes.")
        st.caption(
            "Refreshing this page reconnects to the current test. "
            "One test runs at a time across browser tabs. "
            "Keep the Streamlit service running."
        )

    rendered_state = (task.task_id, task.status) if task is not None else None

    @st.fragment(run_every="1s")
    def show_task():
        current = manager.snapshot()
        current_state = (
            (current.task_id, current.status) if current is not None else None
        )
        if current_state != rendered_state:
            # A full rerun also locks/unlocks the sidebar when task state changes.
            st.rerun()

        if current is None:
            st.info("Choose a Tool and Test Type in the sidebar, then click Start Test.")
            return

        st.write("**Task ID:**", current.task_id)
        if current.status == "running":
            st.info(
                f"Running {current.config.test_type} test with "
                f"{tool_labels.get(current.config.tool_id, current.config.tool_id)} "
                f"— {current.duration_seconds} s elapsed. "
                "You can refresh this page without restarting the test."
            )
        elif current.status == "failed":
            st.error(f"Test failed: {current.error}")
        else:
            failure_count = current.result.get("summary", {}).get("tool_failure_count", 0)
            if failure_count:
                st.warning(
                    f"Test completed with {failure_count} tool failure(s). "
                    "See the results for timeout or other tool errors. "
                    "Completion does not mean all cases passed."
                )
            else:
                st.success("Test completed.")

    show_task()

    # Keep reports outside the polling fragment so expanded results aren't redrawn
    # every second while users read them.
    result = task.result if task is not None and task.status == "completed" else manager.last_result()
    if result is not None:
        if task is not None and task.status != "completed":
            st.caption("Previous successful test result")
        render_result(result)


if __name__ == "__main__":
    main()
