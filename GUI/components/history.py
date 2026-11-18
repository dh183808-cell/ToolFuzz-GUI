"""Read-only selection of persisted runs; execution stays in the existing UI."""

from datetime import datetime, timezone

from GUI.i18n import tr
from GUI.services.result_view import mapping
from main.core import run_store


def history_options(runs):
    """Order RunStore records by their recorded time, newest first."""
    def recorded_time(run):
        metadata = mapping(run.get("metadata"))
        for field in ("started_at", "finished_at"):
            value = metadata.get(field)
            if isinstance(value, str):
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
                except (ValueError, OverflowError):
                    pass
        return datetime.min.replace(tzinfo=timezone.utc)

    options = {}
    for run in sorted((run for run in runs if isinstance(run, dict)), key=recorded_time, reverse=True):
        metadata = mapping(run.get("metadata"))
        task_id = metadata.get("task_id") or metadata.get("run_id")
        if isinstance(task_id, str) and task_id.strip():
            options.setdefault(task_id, run)
    return options


def history_label(run, language):
    metadata = mapping(run.get("metadata"))

    def text(value):
        return value if isinstance(value, str) and value.strip() else tr(language, "not_recorded")

    kind = run.get("test_type")
    kind = tr(language, kind.lower()) if isinstance(kind, str) and kind.lower() in ("runtime", "correctness") else text(kind)
    status = execution_status(run.get("status"), language)
    return " · ".join([
        text(metadata.get("started_at") or metadata.get("finished_at")), kind,
        text(run.get("tool")), text(run.get("model")), status,
        tr(language, "task_id") + ": " + text(metadata.get("task_id")),
    ])


def execution_status(status, language):
    if status in ("completed", "failed", "running", "cancelling", "cancelled"):
        return tr(language, "execution_" + status)
    return status if isinstance(status, str) and status.strip() else tr(language, "not_recorded")


def render_history(language):
    import streamlit as st
    from GUI.components.results import render_result

    st.subheader(tr(language, "history"))
    st.caption(tr(language, "history_note"))
    st.button(tr(language, "history_refresh"), key="history_refresh")
    try:
        options = history_options(run_store.list_runs())
    except (OSError, ValueError, RuntimeError, RecursionError):
        st.warning(tr(language, "history_unreadable"))
        return
    if not options:
        st.info(tr(language, "history_empty"))
        return

    previous = st.session_state.get("history_task", st.query_params.get("history_task"))
    if previous not in options:
        if previous is not None:
            st.info(tr(language, "history_unavailable"))
        st.session_state.history_task = next(iter(options))
    elif "history_task" not in st.session_state:
        st.session_state.history_task = previous
    selected = st.selectbox(
        tr(language, "history_select"), list(options),
        format_func=lambda task_id: history_label(options[task_id], language),
        key="history_task",
    )
    st.query_params["history_task"] = selected
    try:
        result = run_store.load_run(selected)
    except (OSError, ValueError, RuntimeError, RecursionError):
        result = None
    if result is None:
        st.warning(tr(language, "history_unavailable"))
        return

    metadata = mapping(result.get("metadata"))
    st.caption(
        tr(language, "task_id") + ": " + str(metadata.get("task_id") or "—")
        + " · " + tr(language, "run_id") + ": " + str(metadata.get("run_id") or "—")
    )
    st.caption(tr(language, "history_execution", status=execution_status(result.get("status"), language)))
    persistence = mapping(result.get("persistence"))
    st.caption(tr(language, "history_persistence", status=persistence.get("status") or tr(language, "not_recorded")))
    if result.get("status") == "failed":
        st.error(tr(language, "history_failure", error=str(result.get("error") or tr(language, "not_recorded"))))
    if result.get("status") == "cancelled":
        st.info(tr(language, "cancelled"))
    if mapping(result.get("artifacts")).get("save_error"):
        st.warning(tr(language, "history_report_error", error=str(result["artifacts"]["save_error"])))
    render_result(
        result, language, report_root=run_store.DEFAULT_RESULTS_DIR,
        key_prefix="history_" + selected,
    )
