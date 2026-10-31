import streamlit as st

from main.core.correctness_runner import run_correctness
from main.core.model_provider import DEFAULT_MODEL
from main.core.runtime_runner import run_runtime
from main.core.tool_registry import create_tool, list_tool_options


st.set_page_config(
    page_title="ToolFuzz GUI",
    page_icon="🧪",
    layout="wide",
)

st.title("ToolFuzz GUI")
st.caption(
    "Minimal interface for running ToolFuzz Runtime and Correctness tests."
)

# ============================================================
# 1. Configuration
# ============================================================

tool_options = list_tool_options()
tool_labels = {
    option["name"]: option["id"]
    for option in tool_options
}

with st.sidebar:
    st.header("Test Configuration")

    test_type = st.selectbox(
        "Test Type",
        options=["Runtime", "Correctness"],
    )

    selected_tool_name = st.selectbox(
        "Tool",
        options=list(tool_labels.keys()),
    )

    model_name = st.text_input(
        "Model",
        value=DEFAULT_MODEL,
    )

    if test_type == "Runtime":
        iterations = st.number_input(
            "Fuzzer Iterations",
            min_value=1,
            max_value=100,
            value=1,
            step=1,
        )

        prompt_temperature = st.number_input(
            "Prompt Temperature",
            min_value=0.0,
            max_value=2.0,
            value=0.0,
            step=0.1,
        )

        agent_temperature = st.number_input(
            "Agent Temperature",
            min_value=0.0,
            max_value=2.0,
            value=0.0,
            step=0.1,
        )

    else:
        iterations = st.number_input(
            "Prompt Set Iterations",
            min_value=1,
            max_value=100,
            value=1,
            step=1,
        )

        prompt_temperature = st.number_input(
            "Prompt Temperature",
            min_value=0.0,
            max_value=2.0,
            value=0.4,
            step=0.1,
        )

        agent_temperature = st.number_input(
            "Agent Temperature",
            min_value=0.0,
            max_value=2.0,
            value=0.0,
            step=0.1,
        )

        judge_temperature = st.number_input(
            "Judge Temperature",
            min_value=0.0,
            max_value=2.0,
            value=0.0,
            step=0.1,
        )

    start_test = st.button(
        "Start Test",
        type="primary",
        use_container_width=True,
    )

# ============================================================
# 2. Run test
# ============================================================

if "last_result" not in st.session_state:
    st.session_state.last_result = None

if start_test:
    tool_id = tool_labels[selected_tool_name]

    try:
        tool = create_tool(tool_id)

        with st.spinner(
            f"Running {test_type} test with {selected_tool_name}..."
        ):
            if test_type == "Runtime":
                result = run_runtime(
                    tool=tool,
                    model_name=model_name,
                    fuzzer_iters=int(iterations),
                    prompt_temperature=float(prompt_temperature),
                    agent_temperature=float(agent_temperature),
                    output_name="gui_runtime_latest",
                    title="ToolFuzz GUI - Runtime",
                )
            else:
                result = run_correctness(
                    tool=tool,
                    model_name=model_name,
                    prompt_set_iters=int(iterations),
                    prompt_temperature=float(prompt_temperature),
                    agent_temperature=float(agent_temperature),
                    judge_temperature=float(judge_temperature),
                    output_name="gui_correctness_latest",
                    title="ToolFuzz GUI - Correctness",
                )

        st.session_state.last_result = result
        st.success("Test completed.")

    except Exception as exc:
        st.session_state.last_result = None
        st.error(f"Test failed: {type(exc).__name__}: {exc}")

# ============================================================
# 3. Results
# ============================================================

result = st.session_state.last_result

if result is None:
    st.info(
        "Choose a Tool and Test Type in the sidebar, then click Start Test."
    )
else:
    st.subheader("Run Summary")

    metadata = result.get("metadata", {})
    summary = result.get("summary", {})
    artifacts = result.get("artifacts", {})

    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Status", result.get("status", "-"))
    col2.metric("Test Type", result.get("test_type", "-"))
    col3.metric("Tool", result.get("tool", "-"))
    col4.metric(
        "Duration",
        f"{metadata.get('duration_seconds', 0)} s",
    )

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
