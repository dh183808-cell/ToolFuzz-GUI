"""Native Streamlit results dashboard, details, traces and artifact downloads."""

import json

import streamlit as st

from GUI.i18n import tr
from GUI.services.result_view import (
    case_records, dashboard, groups, mapping, matches, parse_trace,
    read_report, records, result_kind, verdict,
)


def show_value(label, value, language):
    st.markdown(f"**{label}**")
    if value is None or value == "":
        st.caption(tr(language, "not_recorded"))
    elif isinstance(value, (dict, list)):
        st.json(value)
    else:
        st.code(str(value), language=None, wrap_lines=True)


def yes_no(value, language):
    return tr(language, "yes" if value is True else "no" if value is False else "unknown")


def reset_stale_selection(key, options):
    # Legacy results can lack run_id and reuse a widget key with fewer records.
    if key in st.session_state and st.session_state[key] not in options:
        st.session_state[key] = options[0]


def recorded_output(record, field, role):
    if field in record:
        return record[field]
    events, _ = parse_trace(record.get("trace"))
    if role == "assistant":
        final = events[-1] if events else {}
        if final.get("role") in ("assistant", "ai") and not final.get("tool_calls"):
            return final.get("content")
        return None
    contents = [event.get("content") for event in events if event.get("role") == role and event.get("content")]
    return contents if contents else None


def render_case(record, kind, language):
    state = verdict(record, kind)
    message = tr(language, "verdict") + ": " + tr(language, state)
    if state in ("tool_failures", "not_triggered"):
        st.warning(message)
    elif state == "triggered":
        st.info(message)
    else:
        st.caption(message)
    show_value(tr(language, "input"), record.get("prompt"), language)
    left, right = st.columns(2)
    with left:
        show_value(tr(language, "tool_arguments"), record.get("invocation_params") if kind == "runtime" else record.get("tool_arguments"), language)
        if kind == "runtime":
            show_value(tr(language, "fuzzed_arguments"), record.get("fuzzed_params"), language)
            show_value(tr(language, "expected_exception"), record.get("expected_exception"), language)
    with right:
        show_value(tr(language, "tool_output"), recorded_output(record, "tool_output", "tool"), language)
        show_value(tr(language, "agent_output"), recorded_output(record, "agent_output", "assistant"), language)
        if kind == "runtime":
            show_value(tr(language, "exception"), record.get("exception"), language)
        elif record.get("tool_failure") is True:
            st.caption(tr(language, "skipped"))
        else:
            show_value(tr(language, "score"), record.get("unexpected_agent_output"), language)
    if kind == "correctness":
        show_value(tr(language, "reason"), record.get("llm_agent_out_reason"), language)


def render_details(result, language, prefix):
    kind = result_kind(result)
    if not case_records(result):
        st.info(tr(language, "empty_results"))
        return
    options = ["all"] + (["tool_failures", "no_failure"] if kind == "correctness" else ["triggered", "not_triggered"]) + ["unknown"]
    reset_stale_selection(f"{prefix}_filter", options)
    selected = st.selectbox(tr(language, "filter"), options, format_func=lambda key: tr(language, key), key=f"{prefix}_filter")
    visible = 0
    if kind == "correctness":
        for group_index, group in enumerate(groups(result), 1):
            cases = [(index, case) for index, case in enumerate(records(group.get("individual_run_test_results")), 1) if matches(case, kind, selected)]
            if not cases:
                continue
            with st.expander(tr(language, "group", index=group_index), expanded=False):
                show_value(tr(language, "template"), group.get("template_question"), language)
                show_value(tr(language, "expected_answer"), group.get("llm_output_expectation"), language)
                if all(case.get("tool_failure") is True for case in records(group.get("individual_run_test_results"))):
                    st.caption(tr(language, "comparison_unavailable"))
                else:
                    st.caption(
                        tr(language, "arguments_inconsistent") + ": " + yes_no(group.get("tool_arguments_inconsistency"), language)
                        + " · " + tr(language, "output_inconsistent") + ": " + yes_no(group.get("tool_output_inconsistency"), language)
                    )
                for case_index, case in cases:
                    st.markdown(f"#### {tr(language, 'case', index=case_index)}")
                    render_case(case, kind, language)
                    visible += 1
    else:
        for index, case in enumerate(case_records(result), 1):
            if matches(case, kind, selected):
                with st.expander(tr(language, "case", index=index) + " · " + tr(language, verdict(case, kind))):
                    render_case(case, kind, language)
                visible += 1
    if not visible:
        st.info(tr(language, "no_matches"))


def render_trace(record, language):
    raw = record.get("trace")
    events, message = parse_trace(raw)
    if message:
        st.info(tr(language, message))
    for index, event in enumerate(events):
        role = event.get("role")
        raw_calls = event.get("tool_calls")
        calls = records(raw_calls)
        if role in ("user", "human"):
            key = "user_event"
        elif role in ("assistant", "ai"):
            key = "final_response" if index == len(events) - 1 and not raw_calls and event.get("content") else "assistant_event"
        elif role == "tool":
            key = "tool_response"
        else:
            key = "event"
        with st.expander(f"{index + 1:02d} · {tr(language, key)}", expanded=False):
            show_value(tr(language, "content"), event.get("content"), language)
            for call_index, call in enumerate(calls, 1):
                function = mapping(call.get("function"))
                st.markdown(f"**{tr(language, 'tool_call')} {call_index} · {function.get('name', call.get('name', ''))}**")
                arguments = function.get("arguments", call.get("args"))
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except (ValueError, RecursionError):
                        pass
                show_value(tr(language, "arguments"), arguments, language)
            st.caption(tr(language, "event_data"))
            st.json(event)
    if raw is not None and raw != "":
        with st.expander(tr(language, "raw_trace")):
            if isinstance(raw, (dict, list)):
                st.json(raw)
            else:
                st.code(str(raw), language=None, wrap_lines=True)


def render_traces(result, language, prefix):
    indexed = []
    if result_kind(result) == "correctness":
        for group_index, group in enumerate(groups(result), 1):
            for case_index, case in enumerate(records(group.get("individual_run_test_results")), 1):
                indexed.append((tr(language, "group", index=group_index) + " · " + tr(language, "case", index=case_index), case))
    else:
        indexed = [(tr(language, "case", index=index), case) for index, case in enumerate(case_records(result), 1)]
    if not indexed:
        st.info(tr(language, "trace_empty"))
        return
    reset_stale_selection(f"{prefix}_trace", range(len(indexed)))
    selected = st.selectbox(tr(language, "select_trace"), range(len(indexed)), format_func=lambda index: indexed[index][0], key=f"{prefix}_trace")
    render_trace(indexed[selected][1], language)


def render_reports(result, language, prefix, *, report_root=None):
    for column, kind in zip(st.columns(2), ("json", "html")):
        with column:
            name, data, message, error = read_report(
                result.get("artifacts"), kind, allowed_root=report_root,
            )
            if message:
                st.info(tr(language, message, kind=kind.upper(), error=error))
            else:
                st.download_button(
                    tr(language, f"download_{kind}"), data=data, file_name=name,
                    mime="application/json" if kind == "json" else "text/html",
                    key=f"{prefix}_download_{kind}", on_click="ignore", width="stretch",
                )


def render_result(result, language, *, report_root=None, key_prefix=None):
    result = mapping(result)
    kind = result_kind(result)
    data = dashboard(result)
    metadata = mapping(result.get("metadata"))
    prefix = key_prefix or str(metadata.get("run_id") or "last_result")
    st.subheader(tr(language, "dashboard"))
    labels = ("prompt_sets", "prompts", "tool_failures") if kind == "correctness" else ("records", "triggered", "not_triggered")
    values = (data["groups"], data["count"], data["yes"]) if kind == "correctness" else (data["count"], data["yes"], data["no"])
    for column, label, value in zip(st.columns(4), (*labels, "duration"), (*values, data["duration"])):
        column.metric(tr(language, label), tr(language, "seconds", value=value) if label == "duration" and value is not None else "—" if value is None else value)
    st.caption(tr(language, "correctness_note" if kind == "correctness" else "runtime_note"))
    if data["unknown"]:
        st.caption(tr(language, "unknown_count", count=data["unknown"]))
    st.caption(
        tr(language, "model") + ": " + str(result.get("model", "—"))
        + " · " + tr(language, "tool") + ": " + str(result.get("tool", "—"))
        + " · " + tr(language, "run_id") + ": " + str(metadata.get("run_id", "—"))
    )

    detail_tab, trace_tab, report_tab, raw_tab = st.tabs([tr(language, key) for key in ("details", "trace", "reports", "raw_json")])
    with detail_tab:
        if data["count"]:
            with st.expander(tr(language, "distribution")):
                categories = ["tool_failures", "no_failure", "unknown"] if kind == "correctness" else ["triggered", "not_triggered", "unknown"]
                category_column = tr(language, "verdict")
                count_column = tr(language, "records")
                st.bar_chart(
                    {category_column: [tr(language, key) for key in categories], count_column: [data["yes"], data["no"], data["unknown"]]},
                    x=category_column, y=count_column, horizontal=True, height=180,
                    color="#4F75A3", x_label=count_column, y_label=category_column,
                )
        render_details(result, language, prefix)
    with trace_tab:
        render_traces(result, language, prefix)
    with report_tab:
        render_reports(result, language, prefix, report_root=report_root)
    with raw_tab:
        st.caption(tr(language, "raw_note"))
        st.json(result)
