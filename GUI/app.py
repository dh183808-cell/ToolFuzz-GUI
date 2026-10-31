import streamlit as st

from GUI.components.results import render_result
from GUI.i18n import LANGUAGES, tr
from GUI.services.result_view import dashboard
from GUI.services.task_manager import RunConfig, get_task_manager
from main.core.model_provider import DEFAULT_MODEL
from main.core.tool_registry import list_tool_options


def language_changed():
    # Change only our URL parameter; preserve the task recovery marker.
    st.query_params["lang"] = st.session_state.language


def init_language():
    if "language" not in st.session_state:
        requested = st.query_params.get("lang", "zh")
        st.session_state.language = requested if requested in LANGUAGES else "zh"
    if st.session_state.language not in LANGUAGES:
        st.session_state.language = "zh"
    if st.query_params.get("lang") != st.session_state.language:
        st.query_params["lang"] = st.session_state.language
    return st.session_state.language


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
        st.session_state.start_error = "empty_model"
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


def main():
    language = init_language()
    st.set_page_config(page_title=tr(language, "title"), page_icon="🧪", layout="wide")
    st.title(tr(language, "title"))
    st.caption(tr(language, "subtitle"))

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
        st.warning(tr(language, "stale_task"))

    tool_labels = {
        option["id"]: tr(language, option["id"])
        for option in list_tool_options()
    }
    with st.sidebar:
        st.radio(
            tr(language, "language"), list(LANGUAGES),
            format_func=LANGUAGES.get, key="language", horizontal=True,
            on_change=language_changed,
        )
        st.header(tr(language, "configuration"))
        test_type = st.selectbox(
            tr(language, "test_type"), ["Runtime", "Correctness"],
            format_func=lambda value: tr(language, value.lower()),
            key="test_type", disabled=running,
        )
        st.selectbox(
            tr(language, "tool"), options=list(tool_labels),
            format_func=tool_labels.get, key="tool_id", disabled=running,
        )
        st.text_input(tr(language, "model"), key="model_name", disabled=running)
        st.number_input(
            tr(language, "fuzzer_iterations" if test_type == "Runtime" else "prompt_iterations"),
            min_value=1, max_value=100, step=1, key="iterations", disabled=running,
        )
        st.number_input(
            tr(language, "prompt_temperature"), min_value=0.0, max_value=2.0, step=0.1,
            key=f"{test_type.lower()}_prompt_temperature", disabled=running,
        )
        st.number_input(
            tr(language, "agent_temperature"), min_value=0.0, max_value=2.0, step=0.1,
            key="agent_temperature", disabled=running,
        )
        if test_type == "Correctness":
            st.number_input(
                tr(language, "judge_temperature"), min_value=0.0, max_value=2.0,
                step=0.1, key="judge_temperature", disabled=running,
            )
        st.button(
            tr(language, "start"), key="start_test", type="primary", width="stretch",
            disabled=running, on_click=start_test,
            args=(manager, task.task_id if task is not None else None),
        )
        if st.session_state.get("start_error"):
            st.error(tr(language, st.session_state.start_error))
        if running:
            st.info(tr(language, "locked"))
        st.caption(tr(language, "service_note"))

    rendered_state = (task.task_id, task.status) if task is not None else None

    @st.fragment(run_every="1s")
    def show_task():
        current = manager.snapshot()
        current_state = (current.task_id, current.status) if current is not None else None
        if current_state != rendered_state:
            st.rerun()
        if current is None:
            st.info(tr(language, "idle"))
            return
        st.caption(tr(language, "task_id") + ": " + current.task_id)
        if current.status == "running":
            st.info(tr(
                language, "running", test_type=tr(language, current.config.test_type.lower()),
                tool=tool_labels.get(current.config.tool_id, current.config.tool_id),
                seconds=current.duration_seconds,
            ))
        elif current.status == "failed":
            st.error(tr(language, "failed", error=current.error))
        else:
            # Recompute from records. Older results may not contain a summary count.
            failure_count = dashboard(current.result)["yes"] if current.config.test_type == "Correctness" else 0
            if failure_count:
                st.warning(tr(language, "completed_failures", count=failure_count))
            else:
                st.success(tr(language, "completed"))

    show_task()

    # Keep result interactions outside the timer fragment: they never submit a task.
    result = task.result if task is not None and task.status == "completed" else manager.last_result()
    if result is not None:
        if task is not None and task.status != "completed":
            st.caption(tr(language, "previous_result"))
        render_result(result, language)


if __name__ == "__main__":
    main()
