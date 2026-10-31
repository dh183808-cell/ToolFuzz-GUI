# ToolFuzz GUI 改造源码上下文（供网页版 ChatGPT 阅读）

生成日期：2026-10-30。请先阅读配套的 `PROJECT_SUMMARY_FOR_CHATGPT.md`。

这里汇总了外部改造的全部 24 个 Python 文件，以及分析调用链、兼容性、结果保存和已发现问题所需的 15 个上游文件。文件内容按当前磁盘原样收录；每节的行数和 SHA-256 用于标识本次快照。空的包初始化文件也会列出。

这是一份阅读用源码快照，文件名均相对于项目根目录。它不是可直接运行的单文件程序，也没有包含 ToolFuzz 的全部依赖文件、提示词、资源和测试。

`ToolFuzz/` 为上游仓库，优先保持不变；`GUI/`、`main/`、`scripts/` 为项目作者的改造。文中第三方源码沿用原仓库 MIT 许可证，许可证附在末尾。

未包含虚拟环境、环境变量、API 密钥、Git 元数据、ZIP、图片或历史报告内容。本次没有执行联网实验；源码里的模型名是配置值，不代表本次已核实远端服务可用。

## 文件索引

- `GUI/__init__.py`
- `GUI/app.py`
- `main/__init__.py`
- `main/compat/__init__.py`
- `main/compat/correctness.py`
- `main/compat/react_agent_new.py`
- `main/compat/runtime_tester.py`
- `main/core/__init__.py`
- `main/core/correctness_runner.py`
- `main/core/model_provider.py`
- `main/core/run_result.py`
- `main/core/runtime_runner.py`
- `main/core/tool_registry.py`
- `main/experiments/__init__.py`
- `main/experiments/correctness_pubmed.py`
- `main/experiments/runtime_duckduckgo.py`
- `main/experiments/runtime_fragile.py`
- `main/tests/__init__.py`
- `main/tests/test_compatible_agent.py`
- `main/tests/test_deepseek.py`
- `main/tests/test_deepseek_tool.py`
- `main/tests/test_import.py`
- `main/tests/test_pubmed.py`
- `scripts/setup_dev_path.py`
- `ToolFuzz/setup.py`
- `ToolFuzz/src/toolfuzz/agent_executors/agent_executor.py`
- `ToolFuzz/src/toolfuzz/agent_executors/langchain/react_new.py`
- `ToolFuzz/src/toolfuzz/runtime/runtime_fuzzer.py`
- `ToolFuzz/src/toolfuzz/runtime/fuzz/fuzzer.py`
- `ToolFuzz/src/toolfuzz/runtime/prompt_generation/prompt_generator.py`
- `ToolFuzz/src/toolfuzz/correctness/correctness_fuzzer.py`
- `ToolFuzz/src/toolfuzz/correctness/correctness_oracle.py`
- `ToolFuzz/src/toolfuzz/correctness/prompt_generation/prompt_generator.py`
- `ToolFuzz/src/toolfuzz/correctness/prompt_generation/llm_responses.py`
- `ToolFuzz/src/toolfuzz/prompt_generation/prompt_generator.py`
- `ToolFuzz/src/toolfuzz/tools/info_extractors/tool_wrapper_factory.py`
- `ToolFuzz/src/toolfuzz/tools/info_extractors/langchain_tool_wrapper.py`
- `ToolFuzz/src/toolfuzz/dataclasses.py`
- `ToolFuzz/src/toolfuzz/utils.py`

# 第一部分：项目作者的外部改造

## GUI/__init__.py

行数：0；SHA-256：`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`。

（空文件，仅用于 Python 包初始化。）

## GUI/app.py

行数：203；SHA-256：`95767869b029e49bcb121b8fcbfe544057366dc3e441cbc168d7bc2d2ddcdfdc`。

```python
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
```

## main/__init__.py

行数：0；SHA-256：`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`。

（空文件，仅用于 Python 包初始化。）

## main/compat/__init__.py

行数：0；SHA-256：`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`。

（空文件，仅用于 Python 包初始化。）

## main/compat/correctness.py

行数：396；SHA-256：`81e94db985ad42eb758b09f814455130f83d6ba80ad5000b23ea0287c8dba07f`。

```python
import json

from tqdm import tqdm

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate

from toolfuzz.correctness.correctness_fuzzer import CorrectnessTester

from src.toolfuzz.correctness.correctness_oracle import CorrectnessOracle
from src.toolfuzz.correctness.prompt_generation.prompt_generator import (
    CorrectnessPromptGenerator,
)
from src.toolfuzz.correctness.prompt_generation.llm_responses import (
    CorrectnessResponse,
    RelevanceResponse,
)
from src.toolfuzz.correctness.prompt_generation.prompts import (
    correctness_prompt,
    tool_output_correctness,
    relevance_prompt,
    template_question,
    synonym_prompt,
    llm_expectation_summary_prompt,
    llm_answers_prompt,
    humanize_prompt,
)
from src.toolfuzz.correctness.correctness_fuzzer import (
    callback_manager_serializer,
)
from src.toolfuzz.dataclasses import (
    TestResult,
    PromptSetTestResults,
)
from src.toolfuzz.logging_mixin import LoggingMixin


class CompatibleCorrectnessPromptGenerator(
    CorrectnessPromptGenerator
):
    """
    ToolFuzz correctness prompt generator using
    an injected OpenAI-compatible LLM such as DeepSeek.
    """

    def __init__(
        self,
        tool_extractor,
        tool_context,
        model,
    ):
        # Do NOT call the original __init__ because it creates
        # ChatOpenAI(model="gpt-4o").
        LoggingMixin.__init__(self)

        self.tool_prompt = tool_extractor.get_tool_docs()

        # Inject our DeepSeek ChatOpenAI object directly.
        self.model = model

        # Preserve ToolFuzz's original prompts.
        self.prompt = template_question
        self.synonym_prompt = synonym_prompt
        self.llm_expectations = llm_expectation_summary_prompt
        self.answer_prompt = llm_answers_prompt

        self.generated_args = []
        self.tool_context = tool_context


class CompatibleCorrectnessOracle(CorrectnessOracle):
    """
    ToolFuzz correctness judge using an injected LLM.
    """

    def __init__(self, model):
        # Again: do NOT call original __init__,
        # because it creates GPT-4o.
        LoggingMixin.__init__(self)

        # -------------------------------------------------
        # 1. Agent answer correctness
        # -------------------------------------------------

        parser = JsonOutputParser(
            pydantic_object=CorrectnessResponse
        )

        prompt = PromptTemplate(
            template=correctness_prompt,
            input_variables=[
                "tool_output",
                "agent_output",
                "expected",
            ],
            partial_variables={
                "format_instructions":
                    parser.get_format_instructions()
            },
        )

        self.chain = prompt | model | parser

        # -------------------------------------------------
        # 2. Tool output correctness
        # -------------------------------------------------

        parser = JsonOutputParser(
            pydantic_object=CorrectnessResponse
        )

        prompt = PromptTemplate(
            template=tool_output_correctness,
            input_variables=[
                "tool_output",
                "expected",
            ],
            partial_variables={
                "format_instructions":
                    parser.get_format_instructions()
            },
        )

        self.tool_out_chain = prompt | model | parser

        # -------------------------------------------------
        # 3. Agent response relevance
        # -------------------------------------------------

        parser = JsonOutputParser(
            pydantic_object=RelevanceResponse
        )

        prompt = PromptTemplate(
            template=relevance_prompt,
            input_variables=[
                "question",
                "tool_output",
                "agent_out",
                "llm_expect",
            ],
            partial_variables={
                "format_instructions":
                    parser.get_format_instructions()
            },
        )

        self.relevance_chain = prompt | model | parser


class CompatibleCorrectnessTester(CorrectnessTester):
    """
    CorrectnessTester compatible with injected DeepSeek models.

    The original ToolFuzz test flow is preserved; this wrapper only
    supplies the compatible prompt generator and oracle through
    externally created models.
    """

    def __init__(
        self,
        *args,
        prompt_model,
        judge_model,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.prompt_model = prompt_model
        self.judge_model = judge_model

    def test(self):
        oracle = CompatibleCorrectnessOracle(
            self.judge_model
        )

        prompt_gen = CompatibleCorrectnessPromptGenerator(
            self.tool_extractor,
            self.context,
            self.prompt_model,
        )

        prompt_set_iters = 0

        while True:
            prompts = prompt_gen.generate_prompt()

            for template, prompt_set, llm_expect in tqdm(
                prompts,
                desc="Testing prompt sets",
            ):
                print(
                    f"\n({prompt_set_iters}/"
                    f"{self.prompt_set_iters})"
                )

                print(
                    "Template:",
                    template,
                )

                print(
                    "Expected answer:",
                    llm_expect,
                )

                if (
                    prompt_set_iters
                    >= self.prompt_set_iters
                ):
                    return

                prompt_set_iters += 1

                outs = []
                individual_test_results = []

                # ----------------------------------------
                # Execute prompts through Agent + Tool
                # ----------------------------------------

                for prompt in tqdm(
                    prompt_set,
                    desc="Running prompt set",
                ):
                    print(
                        "\nPrompt:",
                        prompt,
                    )

                    if len(prompt) == 0:
                        continue

                    agent_result, test_result = (
                        self.run_prompt(prompt)
                    )

                    if agent_result is None:
                        individual_test_results.append(
                            test_result
                        )
                    else:
                        outs.append(
                            (
                                agent_result,
                                prompt,
                            )
                        )

                # ----------------------------------------
                # Original ToolFuzz consistency checks
                # ----------------------------------------

                (
                    tool_output_inconsistency,
                    output_buckets,
                ) = oracle.evaluate_tool_output(
                    outs
                )

                (
                    tool_arguments_inconsistency,
                    args_buckets,
                ) = oracle.evaluate_tool_arguments(
                    outs
                )

                # ----------------------------------------
                # Judge each answer
                # ----------------------------------------

                for agent_res, prompt in outs:
                    if isinstance(
                        agent_res.tool_output,
                        str,
                    ):
                        tool_out_str = (
                            agent_res.tool_output
                        )

                    elif isinstance(
                        agent_res.tool_output,
                        list,
                    ):
                        tool_out_str = "\n".join(
                            agent_res.tool_output
                        )

                    else:
                        tool_out_str = str(
                            agent_res.tool_output
                        )

                    (
                        correctness_degree,
                        reason,
                    ) = oracle.agent_out_correctness(
                        agent_res.agent_response,
                        tool_out_str,
                        llm_expect,
                    )

                    agent_output_relevant = True

                    if (
                        correctness_degree < 5
                        and
                        not tool_output_inconsistency
                    ):
                        (
                            agent_output_relevant,
                            _,
                        ) = (
                            oracle.is_agent_output_relevant(
                                agent_res.agent_response,
                                prompt,
                                tool_out_str,
                                llm_expect,
                            )
                        )

                    individual_test_results.append(
                        TestResult(
                            prompt=prompt,

                            tool_arguments=(
                                self.tool_args_str(
                                    agent_res.tool_args
                                )
                            ),

                            tool_output=tool_out_str,

                            agent_output=(
                                agent_res.agent_response
                            ),

                            tool_failure=(
                                agent_res
                                .is_raised_exception
                            ),

                            unexpected_agent_output=(
                                correctness_degree
                            ),

                            agent_output_not_relevant=(
                                not agent_output_relevant
                            ),

                            llm_agent_out_reason=reason,

                            trace=json.dumps(
                                agent_res.trace,
                                default=(
                                    callback_manager_serializer
                                ),
                            ),
                        )
                    )

                self.results.append(
                    PromptSetTestResults(
                        tool=(
                            self.tool_extractor
                            .get_tool_name()
                        ),

                        template_question=template,

                        template_prompts=prompt_set,

                        same_arguments_buckets=(
                            args_buckets
                        ),

                        same_output_buckets=(
                            output_buckets
                        ),

                        tool_arguments_inconsistency=(
                            tool_arguments_inconsistency
                        ),

                        tool_output_inconsistency=(
                            tool_output_inconsistency
                        ),

                        llm_output_expectation=(
                            llm_expect
                        ),

                        individual_run_test_results=(
                            individual_test_results
                        ),
                    )
                )
```

## main/compat/react_agent_new.py

行数：289；SHA-256：`727dcf06eb77fb4db8a4a29420f889ad455872201d735405481dac8b1ebd3780`。

```python
import json

from toolfuzz.agent_executors.langchain.react_new import ReactAgentNew


class CompatibleReactAgentNew(ReactAgentNew):
    """
    Compatibility wrapper for ToolFuzz ReactAgentNew.

    Fixes:
    1. LangChain 1.x stores tool calls in AIMessage.tool_calls
    2. ToolFuzz assumes JSON-decodable ToolMessage must be {"error": ...}
    3. Tool output may not always be a string
    """

    # =========================================================
    # Fix 1: extract tool arguments from new + old LangChain
    # =========================================================

    def get_tool_arguments(self, agent_result):
        from langchain_core.messages import AIMessage

        for message in agent_result["messages"]:
            if not isinstance(message, AIMessage):
                continue

            # LangChain 1.x
            tool_calls = getattr(message, "tool_calls", None)

            if tool_calls:
                tool_call = tool_calls[0]
                args = tool_call.get("args", {})

                if isinstance(args, str):
                    return args

                return json.dumps(
                    args,
                    ensure_ascii=False,
                )

            # Old LangChain format
            legacy_tool_calls = message.additional_kwargs.get(
                "tool_calls"
            )

            if legacy_tool_calls:
                tool_call = legacy_tool_calls[0]
                return tool_call["function"]["arguments"]

        return "Tool invocation arguments unavailable"


    # =========================================================
    # Fix 2: robust runtime-error extraction
    # =========================================================

    def extract_error(self, trace):

        for item in trace:

            if item.get("role") != "tool":
                continue

            raw_content = item.get("content", "")

            if not isinstance(raw_content, str):
                raw_content = str(raw_content)

            # ---------------------------------------------
            # Case A:
            # ToolFuzz-style JSON error:
            # {"error": "..."}
            # ---------------------------------------------

            try:
                parsed = json.loads(raw_content)

                # IMPORTANT:
                # json.loads("42") -> int
                # json.loads('"hello"') -> str
                # They are normal tool results, not errors.
                if isinstance(parsed, dict):

                    if "error" in parsed:
                        error = parsed.get("error")

                        if error:
                            return True, str(error)

                        return False, ""

            except (json.JSONDecodeError, TypeError):
                pass

            # ---------------------------------------------
            # Case B:
            # Older LangChain textual error
            # ---------------------------------------------

            if "Error: " in raw_content:
                error = raw_content.split(
                    "\n"
                )[0].split(
                    "Error: ",
                    1,
                )[1]

                return True, error

        return False, ""


    # =========================================================
    # Fix 3: robust tool output
    # =========================================================

    def get_tool_output(self, agent_result):
        from langchain_core.messages import ToolMessage

        tool_messages = []

        for message in reversed(agent_result["messages"]):

            if isinstance(message, ToolMessage):

                content = message.content

                if isinstance(content, str):
                    tool_messages.append(content)
                else:
                    tool_messages.append(
                        json.dumps(
                            content,
                            ensure_ascii=False,
                            default=str,
                        )
                    )

        if tool_messages:
            return "\n".join(tool_messages)

        return "No tool output found"


    # =========================================================
    # Fix 4: preserve new LangChain tool calls in trace
    # =========================================================

    def get_trace(self, agent_result):

        from langchain_core.messages import (
            HumanMessage,
            AIMessage,
            ToolMessage,
        )

        transformed_trace = []

        for message in agent_result["messages"]:

            # ---------------- User ----------------

            if isinstance(message, HumanMessage):

                transformed_trace.append(
                    {
                        "role": "user",
                        "content": message.content,
                    }
                )

            # ---------------- Assistant ----------------

            elif isinstance(message, AIMessage):

                formatted_tool_calls = []

                new_tool_calls = getattr(
                    message,
                    "tool_calls",
                    None,
                )

                # LangChain 1.x
                if new_tool_calls:

                    for index, tool_call in enumerate(
                        new_tool_calls
                    ):

                        args = tool_call.get("args", {})

                        if not isinstance(args, str):
                            args = json.dumps(
                                args,
                                ensure_ascii=False,
                            )

                        formatted_tool_calls.append(
                            {
                                "id": tool_call.get(
                                    "id",
                                    index + 1,
                                ),
                                "type": "function",
                                "function": {
                                    "name": tool_call.get(
                                        "name",
                                        "",
                                    ),
                                    "arguments": args,
                                },
                            }
                        )

                # Old LangChain
                else:

                    legacy_tool_calls = (
                        message.additional_kwargs.get(
                            "tool_calls",
                            []
                        )
                    )

                    for index, tool_call in enumerate(
                        legacy_tool_calls
                    ):

                        function = tool_call.get(
                            "function",
                            {},
                        )

                        formatted_tool_calls.append(
                            {
                                "id": tool_call.get(
                                    "id",
                                    index + 1,
                                ),
                                "type": "function",
                                "function": {
                                    "name": function.get(
                                        "name",
                                        "",
                                    ),
                                    "arguments": function.get(
                                        "arguments",
                                        "",
                                    ),
                                },
                            }
                        )

                transformed_trace.append(
                    {
                        "role": "assistant",
                        "content": message.content,
                        "tool_calls": formatted_tool_calls,
                    }
                )

            # ---------------- Tool ----------------

            elif isinstance(message, ToolMessage):

                content = message.content

                if not isinstance(content, str):
                    content = json.dumps(
                        content,
                        ensure_ascii=False,
                        default=str,
                    )

                transformed_trace.append(
                    {
                        "role": "tool",
                        "tool_call_id": getattr(
                            message,
                            "tool_call_id",
                            1,
                        ),
                        "content": content,
                    }
                )

        return transformed_trace
```

## main/compat/runtime_tester.py

行数：246；SHA-256：`b47f79137f0fbdd5f4b2306fbe805eccb3b8374966a1f0f0e9d76e0a980d4a5f`。

```python
import json

from tqdm import tqdm

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate

from toolfuzz.runtime.runtime_fuzzer import RuntimeErrorTester

from src.toolfuzz.dataclasses import TestFailureResult
from src.toolfuzz.runtime.prompt_generation.llm_responses import (
    BadArgumentsPromptResponse,
)
from src.toolfuzz.runtime.prompt_generation.prompts import (
    prompt_from_arguments_prompt,
)


class RuntimeResultMixin:
    """
    Fix ToolFuzz runtime result classification.

    Expected exception:
        successful_trigger = True

    Unexpected framework/runtime exception:
        successful_trigger = False
    """

    def run_test(
        self,
        runnable,
        prompt,
        expected_exception,
        tool_name,
        bad_args,
        agent_type,
    ):
        try:
            agent_result = runnable(prompt)

            exception_text = agent_result.exception

            if not isinstance(exception_text, str):
                exception_text = (
                    str(type(exception_text))
                    + str(exception_text)
                )

            # If the Agent wrapper reports an exception instead
            # of letting it propagate, only count it as successful
            # when it corresponds to the expected exception.
            expected_name = expected_exception.__name__

            matched_expected = (
                agent_result.is_raised_exception
                and expected_name in exception_text
            )

            result = TestFailureResult(
                tool=tool_name,
                expected_exception=str(expected_exception),
                exception=exception_text,
                prompt=prompt,
                agent_type=agent_type,
                invocation_params=agent_result.tool_args,
                fuzzed_params=str(bad_args),
                successful_trigger=matched_expected,
                trace=json.dumps(
                    agent_result.trace,
                    default=str,
                ),
            )

            return (
                expected_exception if matched_expected else None,
                result,
            )

        # This is the failure ToolFuzz was actually looking for.
        except expected_exception as e:

            result = TestFailureResult(
                tool=tool_name,
                expected_exception=str(expected_exception),
                exception=str(type(e)) + str(e),
                prompt=prompt,
                agent_type=agent_type,
                invocation_params="Invocation was interrupted",
                fuzzed_params=str(bad_args),
                successful_trigger=True,
                trace=json.dumps(
                    {"error": str(e)}
                ),
            )

            return expected_exception, result

        # Framework error / unrelated error:
        # DO NOT report it as a successful ToolFuzz trigger.
        except Exception as e:

            result = TestFailureResult(
                tool=tool_name,
                expected_exception=str(expected_exception),
                exception=str(type(e)) + str(e),
                prompt=prompt,
                agent_type=agent_type,
                invocation_params="Invocation was interrupted",
                fuzzed_params=str(bad_args),
                successful_trigger=False,
                trace=json.dumps(
                    {
                        "framework_error": str(e),
                    }
                ),
            )

            return None, result


class CompatibleRuntimePromptGenerator:
    """
    ToolFuzz runtime prompt generator using an injected LLM
    instead of the original hard-coded model.
    """

    def __init__(self, tool_extractor, model):
        self.tool_prompt = tool_extractor.get_tool_docs()
        self.model = model

        self.parser = JsonOutputParser(
            pydantic_object=BadArgumentsPromptResponse
        )

        self.prompt = PromptTemplate(
            template=prompt_from_arguments_prompt,
            input_variables=[
                "tool_prompt",
                "bad_args",
            ],
            partial_variables={
                "format_instructions":
                    self.parser.get_format_instructions()
            },
        )

        self.chain = self.prompt | self.model | self.parser

    def generate_prompt(self, bad_arguments):

        result = self.chain.invoke(
            {
                "tool_prompt": self.tool_prompt,
                "bad_args": bad_arguments,
            }
        )

        if "prompts" not in result:
            raise RuntimeError(
                f"Prompt generator returned invalid result: {result}"
            )

        return result["prompts"]


class CompatibleRuntimeErrorTester(
    RuntimeResultMixin,
    RuntimeErrorTester,
):
    """
    RuntimeErrorTester compatible with the injected DeepSeek LLM
    and the project's runtime result classification.
    """

    def test(self):

        pg = CompatibleRuntimePromptGenerator(
            self.tool_extractor,
            self.model,
        )

        # Original ToolFuzz Fuzzer
        bad_arguments_w_exception = self.fuzzer.fuzz()

        print("\n=== Fuzzer discovered exceptions ===")

        if not bad_arguments_w_exception:
            print("No runtime failures were found.")
            return

        for exception, arguments in bad_arguments_w_exception.items():
            print(f"{exception.__name__}: {arguments}")

        print("====================================\n")

        caught_exceptions = set()

        for exception, bad_arguments in tqdm(
            bad_arguments_w_exception.items(),
            desc="Testing for exceptions",
        ):

            for i in range(len(bad_arguments)):

                if exception in caught_exceptions:
                    break

                bad_arg_examples = bad_arguments[i]

                pre_check = self.sanity_check(
                    self.agent,
                    bad_arg_examples,
                )

                if not pre_check:
                    print(
                        "Sanity check failed for:",
                        bad_arg_examples,
                    )
                    continue

                prompts = pg.generate_prompt(
                    bad_arg_examples
                )

                print("\nGenerated prompts:")

                for prompt in prompts:
                    print(f"  -> {prompt}")

                    thrown_exception, test_result = self.run_test(
                        self.agent,
                        prompt,
                        exception,
                        self.tool_extractor.get_tool_name(),
                        bad_arg_examples,
                        self.agent.get_name(),
                    )

                    self.test_results.append(test_result)

                    if thrown_exception:
                        caught_exceptions.add(
                            thrown_exception
                        )
```

## main/core/__init__.py

行数：0；SHA-256：`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`。

（空文件，仅用于 Python 包初始化。）

## main/core/correctness_runner.py

行数：188；SHA-256：`73926b46d3eb405748c1ca361df8f0570e81bef0b1bc6a1fa7612f0e0c0b31d0`。

```python
from pathlib import Path

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


MAIN_DIR = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS_DIR = MAIN_DIR / "results"


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
    clear_previous=True,
    title="ToolFuzz Correctness Test",
):
    """
    Run one ToolFuzz Correctness test.

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

    if results_dir is None:
        results_dir = DEFAULT_RESULTS_DIR
    else:
        results_dir = Path(results_dir)

    results_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    if output_name is None:
        output_name = f"correctness_{tool_name}"

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

    results = [
        _serialize_result(result)
        for result in tester.results
    ]

    print("\n==========================================")
    print("Finished")
    print(f"Prompt sets: {len(results)}")
    print(f"JSON : {output_path}.json")
    print(f"HTML : {output_path}.html")
    print("==========================================")

    return build_run_result(
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
        },
        results=results,
        json_path=str(output_path) + ".json",
        html_path=str(output_path) + ".html",
    )
```

## main/core/model_provider.py

行数：26；SHA-256：`6d9a131775dd903bc4ba29758d24d67e41407f99fc313792fec95596ef9d5711`。

```python
import os

from langchain_openai import ChatOpenAI


DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"


def create_deepseek_llm(
    model_name=DEFAULT_MODEL,
    temperature=0,
):
    api_key = os.getenv("DEEPSEEK_API_KEY")

    if not api_key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY is not set."
        )

    return ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=DEFAULT_BASE_URL,
        temperature=temperature,
    )
```

## main/core/run_result.py

行数：63；SHA-256：`afee9fe0b662c0dc349c9d36879de3f9645bc0596692ce9a324703c0250476b0`。

```python
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4


@dataclass(frozen=True)
class RunContext:
    run_id: str
    started_at: str
    started_perf: float


def start_run():
    started = datetime.now(timezone.utc)
    run_id = (
        f"{started.strftime('%Y%m%dT%H%M%SZ')}_"
        f"{uuid4().hex[:8]}"
    )
    return RunContext(
        run_id=run_id,
        started_at=started.isoformat(timespec="seconds"),
        started_perf=perf_counter(),
    )


def build_run_result(
    *,
    context,
    status,
    test_type,
    tool,
    model,
    config,
    summary,
    results,
    json_path,
    html_path,
):
    finished = datetime.now(timezone.utc)

    return {
        "status": status,
        "test_type": test_type,
        "tool": tool,
        "model": model,
        "config": config,
        "summary": summary,
        "results": results,
        "artifacts": {
            "json_path": str(json_path),
            "html_path": str(html_path),
        },
        "metadata": {
            "run_id": context.run_id,
            "started_at": context.started_at,
            "finished_at": finished.isoformat(timespec="seconds"),
            "duration_seconds": round(
                perf_counter() - context.started_perf,
                3,
            ),
        },
    }
```

## main/core/runtime_runner.py

行数：190；SHA-256：`7ba52244fb55b139140289ebcec75ab1aa8f45cb07e1c0e74118ea4b18219368`。

```python
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
```

## main/core/tool_registry.py

行数：64；SHA-256：`95202ad26aefb4b5598a2daf483daea4dded78e3e2ea2316b24c8d959d9be144`。

```python
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_community.tools.pubmed.tool import PubmedQueryRun


# ============================================================
# Tool Registry
# ============================================================

_TOOL_FACTORIES = {
    "duckduckgo": DuckDuckGoSearchRun,
    "pubmed": PubmedQueryRun,
}

_TOOL_DISPLAY_NAMES = {
    "duckduckgo": "DuckDuckGo Search",
    "pubmed": "PubMed",
}


def list_tools():
    """
    Return the stable tool IDs currently available to the application.
    """

    return tuple(_TOOL_FACTORIES.keys())


def list_tool_options():
    """
    Return simple metadata suitable for a GUI select box.
    """

    return [
        {
            "id": tool_id,
            "name": _TOOL_DISPLAY_NAMES[tool_id],
        }
        for tool_id in _TOOL_FACTORIES
    ]


def create_tool(tool_id):
    """
    Create and return a fresh LangChain Tool instance.

    Parameters
    ----------
    tool_id:
        Stable application-level tool ID, for example:
        "duckduckgo" or "pubmed".
    """

    normalized_id = tool_id.strip().lower()

    factory = _TOOL_FACTORIES.get(normalized_id)

    if factory is None:
        available = ", ".join(list_tools())
        raise ValueError(
            f"Unknown tool: {tool_id!r}. "
            f"Available tools: {available}"
        )

    return factory()
```

## main/experiments/__init__.py

行数：0；SHA-256：`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`。

（空文件，仅用于 Python 包初始化。）

## main/experiments/correctness_pubmed.py

行数：22；SHA-256：`64b4ca7be81cae0cf842875b3ff944b66292b71584ed251cfa2cd3c0827a7921`。

```python
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
```

## main/experiments/runtime_duckduckgo.py

行数：22；SHA-256：`55d1946bb12b89c1844edb89965df1923a4940ae154311819da56f359c392739`。

```python
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
```

## main/experiments/runtime_fragile.py

行数：32；SHA-256：`6a3de6bb402dfb672d338777a4e8e23858723d5d19909da8a42d174b393239b1`。

```python
from langchain_core.tools import tool

from main.core.runtime_runner import run_runtime


@tool
def fragile_lookup(query: str) -> str:
    """
    Look up an item using the supplied query text.
    """

    # Deliberately introduce a runtime bug for reproduction.
    if len(query) == 0:
        raise ValueError("query must not be empty")

    return f"Lookup result for: {query}"


def main():

    return run_runtime(
        tool=fragile_lookup,
        fuzzer_iters=1,
        prompt_temperature=0.4,
        agent_temperature=0,
        output_name="runtime_minimal",
        title="ToolFuzz Runtime Minimal Reproduction",
    )


if __name__ == "__main__":
    main()
```

## main/tests/__init__.py

行数：0；SHA-256：`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`。

（空文件，仅用于 Python 包初始化。）

## main/tests/test_compatible_agent.py

行数：43；SHA-256：`0042893a029156c9ecc478395e5f67f2050037058983feeca1d2761667d180f2`。

```python
import os

from langchain_core.tools import tool
from main.core.model_provider import create_deepseek_llm

from main.compat.react_agent_new import CompatibleReactAgentNew


@tool
def add_numbers(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


llm = create_deepseek_llm(temperature=0)


agent = CompatibleReactAgentNew(
    add_numbers,
    llm,
)


result = agent(
    "Use add_numbers to calculate 17 + 25."
)


print("Agent response:")
print(result.agent_response)

print("\nTool arguments:")
print(result.tool_args)

print("\nTool output:")
print(result.tool_output)

print("\nTool invoked:")
print(result.is_tool_invoked)

print("\nTrace:")
for item in result.trace:
    print(item)
```

## main/tests/test_deepseek.py

行数：14；SHA-256：`f8c176757a4929fbe178b7d4e7a6d147b4d1c279b077d7b286e3da1633b4d028`。

```python
import os
from main.core.model_provider import create_deepseek_llm

api_key = os.getenv("DEEPSEEK_API_KEY")

if not api_key:
    raise RuntimeError("DEEPSEEK_API_KEY is not set")

llm = create_deepseek_llm(temperature=0.4)


response = llm.invoke("Reply with exactly: DeepSeek connection OK")

print(response.content)
```

## main/tests/test_deepseek_tool.py

行数：31；SHA-256：`bf55032f6d4efa1d59b8ca2ce331b77db31eb015a00f37ea0e2c0633e8930f60`。

```python
import os

from langchain_core.tools import tool
from main.core.model_provider import create_deepseek_llm
from langgraph.prebuilt import create_react_agent


@tool
def add_numbers(a: int, b: int) -> int:
    """Add two integers together."""
    return a + b


llm = create_deepseek_llm(temperature=0)


agent = create_react_agent(
    llm,
    [add_numbers]
)

result = agent.invoke({
    "messages": [
        ("user", "Use the add_numbers tool to calculate 17 + 25.")
    ]
})

for message in result["messages"]:
    print(type(message).__name__)
    print(message)
    print("-" * 60)
```

## main/tests/test_import.py

行数：7；SHA-256：`3c1d286ed7dc9b40052807e8068069be9ed9d8124ea3031470d1b85d56cdbc9d`。

```python
from toolfuzz.runtime.runtime_fuzzer import RuntimeErrorTester
from toolfuzz.correctness.correctness_fuzzer import CorrectnessTester
from toolfuzz.agent_executors.langchain.react_new import ReactAgentNew

print("RuntimeErrorTester import OK")
print("CorrectnessTester import OK")
print("ReactAgentNew import OK")
```

## main/tests/test_pubmed.py

行数：14；SHA-256：`67385390170a650e4a5b86ca6463cd2cdd8e2bdc9f7153305ef041e5a3cd632e`。

```python
from langchain_community.tools.pubmed.tool import PubmedQueryRun


tool = PubmedQueryRun()

query = "CRISPR gene editing"

print("Tool name:")
print(tool.name)

print("\nResult:")
result = tool.invoke(query)

print(result[:2000])
```

## scripts/setup_dev_path.py

行数：75；SHA-256：`2dadebd2fd0a2f3943b4639f5019dd75446259e7c33d2e7287b127ed8e890ba5`。

```python
from pathlib import Path
import site
import sys


# ============================================================
# Resolve workspace paths
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
TOOLFUZZ_ROOT = PROJECT_ROOT / "ToolFuzz"


if not TOOLFUZZ_ROOT.exists():
    raise RuntimeError(
        f"ToolFuzz repository not found: {TOOLFUZZ_ROOT}"
    )


# ============================================================
# Find current virtual environment site-packages
# ============================================================

site_packages_list = site.getsitepackages()

if not site_packages_list:
    raise RuntimeError(
        "Could not locate site-packages."
    )


site_packages = Path(site_packages_list[0])


print("Python executable:")
print(sys.executable)

print("\nSite-packages:")
print(site_packages)

print("\nWorkspace root:")
print(PROJECT_ROOT)

print("\nToolFuzz root:")
print(TOOLFUZZ_ROOT)


# ============================================================
# Create persistent development path
# ============================================================

pth_file = site_packages / "toolfuzz_workspace.pth"

content = (
    f"{PROJECT_ROOT}\n"
    f"{TOOLFUZZ_ROOT}\n"
)

pth_file.write_text(
    content,
    encoding="utf-8",
)


print("\nCreated:")
print(pth_file)

print("\nContents:")
print(content)

print(
    "Development paths installed successfully.\n"
    "Restart Python / VS Code terminal before testing."
)
```

# 第二部分：关键上游实现

## ToolFuzz/setup.py

行数：22；SHA-256：`bdc7fce79f9753da08093d1f6e066c32d4c46e3daeac71733b9da07a885b95fe`。

```python
from setuptools import setup, find_packages

setup(
    name='toolfuzz',
    version='0.1.0',
    package_dir={'': 'src'},
    packages=find_packages(where='src'),
    url='',
    license='MIT',
    author='Ivan Milev',
    author_email='imilev@ethz.ch',
    description='ToolFuzz - Automatic Agent Tool Testing',
    install_requires=[
        'langchain-openai>=0.3.6',
        'langgraph>=0.2.74',
        'langchain>=0.3.19',
        'langchain-core>=0.3.37',
        'faker',
        'jinja2',
        'tqdm',
    ]
)
```

## ToolFuzz/src/toolfuzz/agent_executors/agent_executor.py

行数：83；SHA-256：`edc020cbc4e0bc271f7bbe86210b567b08ec05d638723ce3229ec07941856218`。

```python
from abc import abstractmethod
from dataclasses import dataclass
from typing import List

from src.toolfuzz.environment_import import EnvironmentImportMixin


@dataclass
class AgentResponse:
    agent_response: str
    trace: List[dict]
    is_tool_invoked: bool
    is_raised_exception: bool
    exception: str
    tool_output: str
    tool_args: str


class TestingAgentExecutor(EnvironmentImportMixin):
    def __init__(self, tool, model, **kwargs):
        """
        :param tool: The tool which will be used for the agent
        :param model: The models which will be used for the agent. Has to instance of the model not just name.
        :param kwargs: Additional KWARGS which will be passed to the agent construction.
        """
        self._import_environment()
        self.tool = tool
        self.model = model

    @abstractmethod
    def heartbeat(self) -> bool:
        """
        The method which checks if the agent is working properly with a test call.
        :return: True if the agent is initialized and works properly, False otherwise.
        """
        raise NotImplementedError

    @abstractmethod
    def __call__(self, prompt: str, *args, **kwargs) -> AgentResponse:
        """
        The method which executes the agent call.
        In langchain this is the .invoke() method.
        :param prompt: the prompt with which the agent is called.
        :param args: additional arguments for the agent invocation.
        :param kwargs: additional keyword arguments for the agent invocation.
        :return:
        """
        raise NotImplementedError

    @abstractmethod
    def get_name(self) -> str:
        """
        The name of the agent, it is used for identification in the fuzzing reports.
        :return:
        """
        raise NotImplementedError

    @abstractmethod
    def get_tool_output(self, agent_result) -> str:
        """
        Given the output of the __call__ method, this method should return the output of the tool.
        :param agent_result:
        :return:
        """
        raise NotImplementedError

    @abstractmethod
    def get_trace(self, trace) -> List[dict]:
        """
        Given the output of the __call__ method, this method should return the trace of the agent.
        :param trace:
        :return:
        """
        raise NotImplementedError

    @abstractmethod
    def get_tool_arguments(self, trace) -> str:
        """
        Given the trace from the @get_trace method, this method should return the arguments of the tool call.
        :param trace:
        :return:
        """
        raise NotImplementedError
```

## ToolFuzz/src/toolfuzz/agent_executors/langchain/react_new.py

行数：108；SHA-256：`09650fa2526002f86e58ce94a8c42e0a4e926b2e8484332f328f7579ff945c22`。

```python
import json

from src.eval.buggy_tools.open_street_map import open_street_map_search
from src.toolfuzz.agent_executors.agent_executor import TestingAgentExecutor, AgentResponse


class ReactAgentNew(TestingAgentExecutor):
    def _import_environment(self):
        try:
            from langchain_core.messages import HumanMessage, ToolMessage, AIMessage
            from langgraph.prebuilt import create_react_agent
        except ImportError:
            raise ImportError(
                "Please install langchain-core and langgraph packages to use this agent executor: 'pip install langchain', 'pip install langchain-core' and 'pip install langgraph'")

    def __init__(self, tool, model, **kwargs):
        super().__init__(tool, model)
        from langgraph.prebuilt import create_react_agent
        try:
            if not isinstance(tool, list):
                tool = [tool]
            self.agent = create_react_agent(model, tool, **kwargs)
        except:
            self.agent = None

    def __call__(self, prompt, *args, **kwargs):
        from langchain_core.messages import HumanMessage

        agent_result = self.agent.invoke({'messages': [HumanMessage(content=prompt)]}, **kwargs)
        agent_answer = agent_result['messages'][-1].content
        tool_output = self.get_tool_output(agent_result)
        trace = self.get_trace(agent_result)

        is_tool_invoked = any(['tool' in msg.values() for msg in trace])
        runtime_failure, error = self.extract_error(trace)

        return AgentResponse(agent_response=agent_answer, trace=trace, is_tool_invoked=is_tool_invoked,
                             is_raised_exception=runtime_failure, exception=error, tool_output=tool_output,
                             tool_args=self.get_tool_arguments(agent_result))

    def extract_error(self, trace):
        for item in trace:
            if item['role'] == 'tool':
                try:
                    content = json.loads(item['content'])
                    error = content['error']
                    return error is not None, '' if not error else error
                except ValueError:
                    if 'Error: ' in item['content']:
                        return 'Error: ' in item['content'], item['content'].split('\n')[0].split('Error: ')[1]
        return False, ''

    def heartbeat(self) -> bool:
        try:
            if self.agent is not None:
                self(f"Please come up with parameters for a test run and do a test call to: {self.tool.name}")
                return True
        except:
            return False
        return False

    def get_tool_output(self, agent_result):
        from langchain_core.messages import ToolMessage

        messages = agent_result['messages'].copy()
        messages.reverse()
        tool_messages = []
        for message in messages:
            if isinstance(message, ToolMessage):
                tool_messages.append(message.content)
        if len(tool_messages) > 0:
            return "\n".join(tool_messages)
        return "No tool output found"

    def get_trace(self, trace):
        from langchain_core.messages import HumanMessage, ToolMessage, AIMessage

        transformed_trace = []
        for item in trace['messages']:
            if isinstance(item, HumanMessage):
                transformed_trace.append({'role': 'user', 'content': item.content})
            if isinstance(item, AIMessage):
                tool_calls = []
                if 'tool_calls' in item.additional_kwargs:
                    tool_call = item.additional_kwargs['tool_calls'][0]  # We have only one function
                    tool_calls.append({'id': 1,
                                       "type": "function",
                                       "function": {"name": tool_call["function"]["name"],
                                                    "arguments": tool_call["function"]["arguments"]
                                                    }})
                transformed_trace.append({'role': 'assistant', "content": item.content, "tool_calls": tool_calls})
            if isinstance(item, ToolMessage):
                transformed_trace.append({"role": "tool", "tool_call_id": 1, "content": item.content})

        return transformed_trace

    def get_tool_arguments(self, trace):
        from langchain_core.messages import AIMessage

        for item in trace['messages']:
            if isinstance(item, AIMessage):
                if 'tool_calls' in item.additional_kwargs:
                    tool_call = item.additional_kwargs['tool_calls'][0]  # We have only one function
                    return tool_call["function"]["arguments"]
        return None

    def get_name(self) -> str:
        return 'react_new'
```

## ToolFuzz/src/toolfuzz/runtime/runtime_fuzzer.py

行数：111；SHA-256：`37f4598003414495696ab6192f3f6fe926552903851eda3d060bb2f22a0541f7`。

```python
from typing import Any
import json

from langchain_openai import ChatOpenAI
from tqdm import tqdm

from src.eval.toolfuzz.envs.reset_context import DummyResetContext, ResetContext
from src.toolfuzz.agent_executors.agent_executor import TestingAgentExecutor
from src.toolfuzz.dataclasses import TestFailureResult
from src.toolfuzz.logging_mixin import LoggingMixin
from src.toolfuzz.runtime.fuzz.fuzzer import Fuzzer
from src.toolfuzz.runtime.prompt_generation.prompt_generator import RuntimeFailurePromptGeneration
from src.toolfuzz.tools.info_extractors.tool_wrapper import ToolWrapper
from src.toolfuzz.tools.info_extractors.tool_wrapper_factory import ToolWrapperFactory
from src.toolfuzz.utils import save_test_results, save_test_results_html


class RuntimeErrorTester(LoggingMixin):
    def __init__(self,
                 llm,
                 tool: Any,
                 agent: TestingAgentExecutor,
                 custom_tool_wrapper: ToolWrapper = None,
                 context_resetter: ResetContext = DummyResetContext(),
                 bad_examples_per_param: int = 5,
                 fuzzer_iters: int = 10):
        super().__init__()
        self.model, self.agent, self.tool = llm, agent, tool
        if isinstance(self.model, str):
            self.model = ChatOpenAI(model=self.model)

        self.tool_extractor = ToolWrapperFactory.create_extractor(tool) \
            if custom_tool_wrapper is None else custom_tool_wrapper

        self.fuzzer = Fuzzer(self.tool,
                             max_iterations=fuzzer_iters,
                             custom_tool_extractor=self.tool_extractor)
        self.context_resetter = context_resetter
        self.test_results = []
        self.bad_examples = bad_examples_per_param

    def test(self):
        pg = RuntimeFailurePromptGeneration(self.tool_extractor)
        bad_arguments_w_exception = self.fuzzer.fuzz()
        caught_exceptions = set()
        for exception, bad_arguments in tqdm(bad_arguments_w_exception.items(), desc='Testing for exceptions'):
            for i in range(0, len(bad_arguments)):
                if exception in caught_exceptions:
                    break

                bad_arg_examples = bad_arguments[i]
                pre_check = self.sanity_check(self.agent, bad_arg_examples)
                if not pre_check:
                    continue
                prompts = pg.generate_prompt(bad_arg_examples)
                for prompt in prompts:
                    self.log_info(f'{prompt} for {bad_arg_examples}')
                    thrown_exception, test_result = self.run_test(self.agent, prompt, exception,
                                                                  self.tool_extractor.get_tool_name(),
                                                                  bad_arg_examples,
                                                                  self.agent.get_name())
                    self.test_results.append(test_result)
                    if thrown_exception:
                        caught_exceptions.add(thrown_exception)

    def run_test(self, runnable, prompt, expected_exception, tool_name, bad_args, agent_type):
        try:
            agent_result = runnable(prompt)
            if type(agent_result.exception) != str:
                agent_result.exception = str(type(agent_result.exception)) + str(agent_result.exception)
            return expected_exception, TestFailureResult(tool=tool_name,
                                                         expected_exception=str(expected_exception),
                                                         exception=agent_result.exception,
                                                         prompt=prompt, agent_type=agent_type,
                                                         invocation_params=agent_result.tool_args,
                                                         fuzzed_params=str(bad_args),
                                                         successful_trigger=agent_result.is_raised_exception,
                                                         trace=json.dumps(agent_result.trace))
        except expected_exception as e:
            return expected_exception, TestFailureResult(tool=tool_name,
                                                         expected_exception=str(expected_exception),
                                                         exception=str(type(e)) + str(e),
                                                         prompt=prompt, agent_type=agent_type,
                                                         invocation_params='Invocation was interrupted',
                                                         fuzzed_params=str(bad_args),
                                                         successful_trigger=True,
                                                         trace=json.dumps({'error': str(e)}))
        except Exception as e:
            return type(e), TestFailureResult(tool=tool_name,
                                              expected_exception=str(expected_exception),
                                              exception=str(type(e)) + str(e),
                                              prompt=prompt, agent_type=agent_type,
                                              fuzzed_params=str(bad_args),
                                              invocation_params='Invocation was interrupted',
                                              successful_trigger=True,
                                              trace=json.dumps({'error': str(e)}))
        finally:
            self.context_resetter.reset_context()

    def sanity_check(self, agent_callable, bad_args) -> bool:
        prompt = f"Please invoke the {self.tool_extractor.get_tool_name()} exactly like: {self.tool_extractor.get_tool_declaration_name()}({bad_args})"
        try:
            response = agent_callable(prompt)
            return response.is_tool_invoked and response.is_raised_exception
        except Exception as e:
            self.log_info(f'Sanity check passed for {bad_args}: {e}')
            return True

    def save(self, file='./results_runtime'):
        save_test_results(self.test_results, f"{file}.json")
        save_test_results_html(self.test_results, f"{file}.html")
```

## ToolFuzz/src/toolfuzz/runtime/fuzz/fuzzer.py

行数：76；SHA-256：`da72e15e71c68d05fd9568fab11c6c0af79e2a142faef78a2ef0c697f1a00003`。

```python
import asyncio
import inspect
import json

from src.toolfuzz.logging_mixin import LoggingMixin
from src.toolfuzz.runtime.fuzz.taints import TaintDict, TaintStr
from src.toolfuzz.runtime.fuzz.type_generators import StringGenerator, get_generators
from src.toolfuzz.tools.info_extractors.langchain_tool_wrapper import LangchainToolWrapper


class Fuzzer(LoggingMixin):
    def __init__(self, tool, max_iterations=100, custom_tool_extractor=None):
        super().__init__()
        self.tool_extractor = LangchainToolWrapper(tool) if custom_tool_extractor is None else custom_tool_extractor
        self.max_iterations = max_iterations
        self.tool_args = self.tool_extractor.get_tool_args()
        self.tool = tool
        self.generators = get_generators()
        self.str_seed = TaintStr(''), TaintStr('{}')

    def generate_tool_kwargs(self, iter):
        arguments = {}
        for arg in self.tool_args:
            arg_generator = None
            for gen in self.generators:
                if gen.can_gen(arg.type):
                    arg_generator = gen
                    break
            if arg_generator is None:
                assert arg.has_default, f'No argument generator found for {arg}. Available generators {self.generators}. Tool {self.tool}'
                continue

            if iter < len(self.str_seed) and isinstance(arg_generator, StringGenerator):
                arguments[arg.name] = self.str_seed[iter]
            else:
                arguments[arg.name] = arg_generator.generate(arg.type, iter % 100 == 0)  # every 20 iters change
        return arguments

    def fuzz(self):
        self._clear_state()
        bad_params = {}
        with CustomJsonParseBlock():
            for iter in range(self.max_iterations):
                kwargs = self.generate_tool_kwargs(iter)
                try:
                    self.log_info(f"({iter}/{self.max_iterations}) Generated args {kwargs}")
                    self.tool_extractor.invoke_tool(kwargs)
                except Exception as e:
                    exception_type = type(e)
                    if exception_type in bad_params:
                        bad_params[exception_type].append(kwargs)
                    else:
                        bad_params[exception_type] = [kwargs]
            return bad_params

    @staticmethod
    def _clear_state():
        TaintDict.acc_keys = []


def load_in_taint_dict(dictionary):
    return TaintDict(dictionary)


class CustomJsonParseBlock:
    def __init__(self):
        self.original_json_loads = json.loads

    def __enter__(self):
        def custom_json_loads(*args, **kwargs):
            return self.original_json_loads(object_hook=load_in_taint_dict, *args, **kwargs)

        json.loads = custom_json_loads

    def __exit__(self, exc_type, exc_val, exc_tb):
        json.loads = self.original_json_loads
```

## ToolFuzz/src/toolfuzz/runtime/prompt_generation/prompt_generator.py

行数：16；SHA-256：`69bd1a3885399626901cc349a6e3197a44e6df3bae684b8fe0aa1cc2c2491cb0`。

```python
from src.toolfuzz.prompt_generation.prompt_generator import PromptGenerator
from src.toolfuzz.runtime.prompt_generation.llm_responses import BadArgumentsPromptResponse
from src.toolfuzz.runtime.prompt_generation.prompts import prompt_from_arguments_prompt


class RuntimeFailurePromptGeneration(PromptGenerator):
    def __init__(self, tool_extractor, model='gpt-4o', temperature=0):
        super().__init__(tool_extractor, model, temperature)
        self.prompt = prompt_from_arguments_prompt

    def generate_prompt(self, bad_arguments):
        res = self._generate_from_template(self.prompt, ['tool_prompt', 'query'],
                                           {"tool_prompt": self.tool_prompt, "bad_args": bad_arguments},
                                           BadArgumentsPromptResponse)
        assert 'prompts' in res
        return res['prompts']
```

## ToolFuzz/src/toolfuzz/correctness/correctness_fuzzer.py

行数：134；SHA-256：`331fdf8571b7136514ead5054ec9ddb4e08135b3fb4692b4c1e4a4ba20504de6`。

```python
import json

from langchain_core.callbacks import CallbackManagerForToolRun
from langchain_core.tools import ToolException
from langchain_openai import ChatOpenAI
from tqdm import tqdm

from src.eval.toolfuzz.envs.reset_context import DummyResetContext, ResetContext
from src.toolfuzz.agent_executors.agent_executor import TestingAgentExecutor
from src.toolfuzz.correctness.correctness_oracle import CorrectnessOracle
from src.toolfuzz.correctness.prompt_generation.prompt_generator import CorrectnessPromptGenerator
from src.toolfuzz.dataclasses import TestResult, PromptSetTestResults
from src.toolfuzz.logging_mixin import LoggingMixin
from src.toolfuzz.tools.info_extractors.tool_wrapper_factory import ToolWrapperFactory
from src.toolfuzz.utils import save_test_results, save_test_results_html


class CorrectnessTester(LoggingMixin):
    def __init__(self, llm, tool,
                 agent: TestingAgentExecutor,
                 additional_context: str = '',
                 custom_tool_wrapper=None,
                 context_resetter: ResetContext = DummyResetContext(),
                 prompt_set_iters=10):
        super().__init__()
        self.tool, self.agent = tool, agent
        self.context = additional_context
        self.model = llm
        if isinstance(self.model, str):
            self.model = ChatOpenAI(model=self.model)

        self.prompt_set_iters = prompt_set_iters
        self.results = []
        self.context_resetter = context_resetter
        self.tool_extractor = ToolWrapperFactory.create_extractor(tool) \
            if custom_tool_wrapper is None else custom_tool_wrapper

    def test(self):
        oracle = CorrectnessOracle()
        prompt_set_iters = 0

        prompt_gen = CorrectnessPromptGenerator(self.tool_extractor, self.context)
        while True:
            prompts = prompt_gen.generate_prompt()
            for template, prompt_set, llm_expect in tqdm(prompts, desc='Testing prompt sets'):
                print(f"({prompt_set_iters}/{self.prompt_set_iters}) Testing prompt set: {template}")
                if prompt_set_iters >= self.prompt_set_iters:
                    # The number of prompt sets to test has been reached
                    return

                prompt_set_iters += 1
                outs = []
                individual_test_results = []
                for prompt in tqdm(prompt_set, desc="Running prompt set"):
                    self.log_info(f"Prompt: {prompt}")
                    if len(prompt) == 0:
                        continue
                    agent_result, test_res = self.run_prompt(prompt)
                    if agent_result is None:
                        individual_test_results.append(test_res)
                    else:
                        outs.append((agent_result, prompt))

                tool_output_inconsistency, output_buckets = oracle.evaluate_tool_output(outs)
                tool_arguments_inconsistency, args_buckets = oracle.evaluate_tool_arguments(outs)

                for agent_res, prompt in outs:
                    if isinstance(agent_res.tool_output, str):
                        tool_out_str = agent_res.tool_output
                    elif isinstance(agent_res.tool_output, list):
                        tool_out_str = '\n'.join(agent_res.tool_output)
                    else:
                        tool_out_str = str(agent_res.tool_output)
                    correctness_degree, reason = oracle.agent_out_correctness(agent_res.agent_response,
                                                                              tool_out_str,
                                                                              llm_expect)
                    agent_output_relevant = True
                    if correctness_degree < 5 and not tool_output_inconsistency:
                        agent_output_relevant, _ = oracle.is_agent_output_relevant(agent_res.agent_response, prompt,
                                                                                   tool_out_str, llm_expect)
                    individual_test_results.append(
                        TestResult(prompt=prompt, tool_arguments=self.tool_args_str(agent_res.tool_args),
                                   tool_output=tool_out_str,
                                   agent_output=agent_res.agent_response,
                                   tool_failure=agent_res.is_raised_exception,
                                   unexpected_agent_output=correctness_degree,
                                   agent_output_not_relevant=(not agent_output_relevant),
                                   llm_agent_out_reason=reason,
                                   trace=json.dumps(agent_res.trace, default=callback_manager_serializer)))

                self.results.append(
                    PromptSetTestResults(tool=self.tool_extractor.get_tool_name(), template_question=template,
                                         template_prompts=prompt_set,
                                         same_arguments_buckets=args_buckets,
                                         same_output_buckets=output_buckets,
                                         tool_arguments_inconsistency=tool_arguments_inconsistency,
                                         tool_output_inconsistency=tool_output_inconsistency,
                                         llm_output_expectation=llm_expect,
                                         individual_run_test_results=individual_test_results))

    def run_prompt(self, prompt):
        try:
            agent_result = self.agent(prompt)
            return agent_result, None
        except ToolException as e:
            return None, TestResult(prompt=prompt, tool_arguments='Invalid tool call', tool_output="Tool FAILURE",
                                    agent_output=f"Tool Failure {e}",
                                    tool_failure=True,
                                    unexpected_agent_output=0,
                                    agent_output_not_relevant=True,
                                    llm_agent_out_reason="Tool Failure",
                                    trace="{}")
        finally:
            self.context_resetter.reset_context()

    @staticmethod
    def tool_args_str(args):
        if type(args) == str:
            return args
        if type(args) == dict:
            return str({k: v for k, v in args.items() if k != 'run_manager'})
        return str(args)

    def save(self, file_name='./results_correctness'):
        save_test_results(self.results, f"{file_name}.json")
        save_test_results_html(self.results, f"{file_name}.html")


def callback_manager_serializer(obj):
    if isinstance(obj, CallbackManagerForToolRun):
        return ""  # Replace non-serializable objects with an empty string

    # Use the default behavior happen for other objects
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")
```

## ToolFuzz/src/toolfuzz/correctness/correctness_oracle.py

行数：131；SHA-256：`ee80f911915ba4b5dec9f74f53bdc9fc68b3622455a22d3a38b770305ca501e0`。

```python
from langchain_core.exceptions import OutputParserException
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI

from src.toolfuzz.correctness.prompt_generation.llm_responses import CorrectnessResponse, RelevanceResponse
from src.toolfuzz.correctness.prompt_generation.prompts import correctness_prompt, tool_output_correctness, \
    relevance_prompt
from src.toolfuzz.dataclasses import Bucket
from src.toolfuzz.logging_mixin import LoggingMixin


class CorrectnessOracle(LoggingMixin):
    def __init__(self, model='gpt-4o'):
        super().__init__()
        model = ChatOpenAI(model=model, temperature=0)
        parser = JsonOutputParser(pydantic_object=CorrectnessResponse)
        prompt = PromptTemplate(
            template=correctness_prompt,
            input_variables=['tool_output', 'agent_output', 'expected'],
            partial_variables={"format_instructions": parser.get_format_instructions()})
        self.chain = prompt | model | parser

        parser = JsonOutputParser(pydantic_object=CorrectnessResponse)
        prompt = PromptTemplate(
            template=tool_output_correctness,
            input_variables=['tool_output', 'expected'],
            partial_variables={"format_instructions": parser.get_format_instructions()})
        self.tool_out_chain = prompt | model | parser

        parser = JsonOutputParser(pydantic_object=RelevanceResponse)
        is_out_relevant = PromptTemplate(
            template=relevance_prompt,
            input_variables=['question', 'tool_output', 'agent_out', 'llm_expect'],
            partial_variables={"format_instructions": parser.get_format_instructions()}
        )
        self.relevance_chain = is_out_relevant | model | parser

    def is_tool_output_correct(self, tool_output, expected, attempt=3):
        if attempt == 0:
            return False, 'Failed to parse judge LLM response'
        try:
            response = self.tool_out_chain.invoke({'tool_output': tool_output, 'expected': expected})
            return response['is_correct'], response['reason']
        except OutputParserException as e:
            self.log_error(f"Error during evaluation of the result {e}")
            return self.is_tool_output_correct(tool_output, expected, attempt - 1)
        except KeyError as e:
            self.log_error(f"Error during evaluation of the result {e}")
            return self.is_tool_output_correct(tool_output, expected, attempt - 1)

    def agent_out_correctness(self, agent_answer, tool_message, expected, attempt=3):
        if attempt == 0:
            return False, 'Failed to parse judge LLM response'
        try:
            model_response = self.chain.invoke(
                {'agent_output': agent_answer, 'tool_output': tool_message, 'expected': expected})
            return model_response['correctness_degree'], model_response['reason']
        except OutputParserException:
            self.log_error("Failed to parse judge LLM response")
            return self.is_agent_output_relevant(agent_answer, tool_message, expected, attempt - 1)
        except KeyError as e:
            self.log_error(f"Error during evaluation of the result {e}")
            return self.is_agent_output_relevant(agent_answer, tool_message, expected, attempt - 1)

    def is_agent_output_relevant(self, agent_out, prompt, tool_output, llm_expect, attempt=3):
        if attempt == 0:
            return False, 'Failed to parse judge LLM response'
        try:
            model_response = self.relevance_chain.invoke(
                {'question': prompt, 'tool_output': tool_output, 'agent_out': agent_out, 'llm_expect': llm_expect})
            return model_response['is_relevant'], model_response['reason']
        except OutputParserException:
            self.log_error("Failed to parse judge LLM response")
            return self.is_agent_output_relevant(agent_out, prompt, tool_output, llm_expect, attempt - 1)
        except KeyError as e:
            self.log_error(f"Error during evaluation of the result {e}")
            return self.is_agent_output_relevant(agent_out, prompt, tool_output, llm_expect, attempt - 1)

    @staticmethod
    def evaluate_tool_output(agent_outs):
        # Group and check
        buckets = {}
        for agent_res, prompt in agent_outs:
            tool_out = agent_res.tool_output
            assert isinstance(tool_out, list) or isinstance(tool_out, str)
            if isinstance(tool_out, list):
                if len(tool_out) == 0:
                    add_to_bucket(buckets, '', prompt)
                for out in tool_out:
                    add_to_bucket(buckets, out, prompt)
            elif isinstance(tool_out, str):
                add_to_bucket(buckets, tool_out, prompt)
        # So no special thing if all the outs are the same:
        transformed_buckets = [Bucket(bucket_items=prompts, bucket_value=output) for output, prompts in
                               buckets.items()]
        for bucket in buckets:
            if len(buckets[bucket]) == len(agent_outs):
                return False, transformed_buckets
        return True, transformed_buckets

    @staticmethod
    def evaluate_tool_arguments(outs):
        buckets = {}
        for agent_out, prompt in outs:
            args = agent_out.tool_args
            add_to_bucket(buckets, args, prompt)

        transformed_buckets = [Bucket(bucket_items=prompts, bucket_value=str(args)) for args, prompts in
                               buckets.items()]

        for bucket in buckets:
            if len(buckets[bucket]) == len(outs):
                return False, transformed_buckets
        return True, transformed_buckets


def add_to_bucket(buckets, key, value):
    key = str(key)  # Some tools have structured output i.e. list/dict
    if key in buckets:
        buckets[key].append(value)
    elif key not in buckets:
        buckets[key] = [value]


def flatten(lst):
    for item in lst:
        if isinstance(item, list):
            yield from flatten(item)
        else:
            yield item
```

## ToolFuzz/src/toolfuzz/correctness/prompt_generation/prompt_generator.py

行数：95；SHA-256：`6e9e44a0c1fdff91da2ec055556377e051dc73601147a5d2d0fa49e25b9d2020`。

```python
import random
import re

from src.toolfuzz.correctness.prompt_generation.llm_responses import AgentTemplatePrompts, AgentTemplateArguments, \
    LLMExpectedAnswers, LLMExpectedAnswer, PromptSimplification
from src.toolfuzz.correctness.prompt_generation.prompts import template_question, synonym_prompt, \
    llm_expectation_summary_prompt, \
    llm_answers_prompt, humanize_prompt
from src.toolfuzz.logging_mixin import LoggingMixin
from src.toolfuzz.prompt_generation.prompt_generator import PromptGenerator


class CorrectnessPromptGenerator(PromptGenerator, LoggingMixin):

    def __init__(self, tool_extractor, tool_context, model_name='gpt-4o', temperature=0.4):
        LoggingMixin.__init__(self)
        PromptGenerator.__init__(self, tool_extractor, model_name, temperature)
        self.prompt = template_question
        self.synonym_prompt = synonym_prompt
        self.llm_expectations = llm_expectation_summary_prompt
        self.answer_prompt = llm_answers_prompt
        self.generated_args = []
        self.tool_context = tool_context

    def generate_prompt(self, bad_arguments=None):
        res = self._generate_from_template(self.prompt, ['tool_prompt', 'tool_context'],
                                           {"tool_prompt": self.tool_prompt, 'tool_context': self.tool_context},
                                           AgentTemplatePrompts)
        assert 'template_prompts' in res
        # Now for each questions get the diffs:
        templates = res['template_prompts']
        for template in templates:
            prompt_args = self._generate_from_template(self.synonym_prompt,
                                                       ['template_prompt', 'tool_prompt', 'used_args', 'tool_context'],
                                                       {"template_prompt": template, "tool_prompt": self.tool_prompt,
                                                        "used_args": self.generated_args,
                                                        "tool_context": self.tool_context},
                                                       AgentTemplateArguments)
            self.update_gen_args(prompt_args)
            prompts = self.to_prompts(template, prompt_args)
            if len(prompts) > 4:
                prompts = self.humanize_prompts(prompts)
            answers = self._generate_from_template(self.answer_prompt, ['questions', 'tool_prompt'],
                                                   {'questions': '\n'.join(prompts),
                                                    'tool_prompt': self.tool_prompt},
                                                   LLMExpectedAnswers)
            llm_expectation = self._generate_from_template(self.llm_expectations, ['sentences'],
                                                           {'sentences': '\n'.join(
                                                               answers['expected_answers'])},
                                                           LLMExpectedAnswer)
            if isinstance(llm_expectation, dict) and 'expected_answer' in llm_expectation:
                yield template, prompts, llm_expectation['expected_answer']
            else:
                yield template, prompts, llm_expectation

    def humanize_prompts(self, prompts):
        indx_list = list(range(len(prompts)))
        selection = random.sample(indx_list, int(len(prompts) / 2))
        humanized_prompts = [prompts[i] for i in selection]
        humanized_prompts = self._generate_from_template(humanize_prompt, ['tool_prompt', 'prompts'],
                                                         {'tool_prompt': self.tool_prompt,
                                                          'prompts': humanized_prompts},
                                                         PromptSimplification)['simplified_prompts']
        for i in range(len(prompts)):
            if i in selection:
                prompts[i] = humanized_prompts.pop(0)
            else:
                prompts[i] = prompts[i]

        return prompts

    def update_gen_args(self, prompt_args):
        # Get just the first two
        for args in prompt_args['template_arguments']:
            self.generated_args += args['argument_values'][:2]

    def to_prompts(self, template, prompt_args):
        str_template = re.sub('\[.*?\]', '{}', template)
        arguments = prompt_args['template_arguments']
        str_args_sets = []
        for i in range(len(arguments[0]['argument_values'])):
            str_args = []
            for argument in arguments:
                str_args.append(argument['argument_values'][i])
            if len(str_args_sets) != 0 and len(str_args_sets[-1]) == len(str_args):
                str_args_sets.append(str_args)
            if len(str_args_sets) == 0:
                str_args_sets.append(str_args)

        # now yield the results
        prompts = [str_template.format(*str_args_set) for str_args_set in str_args_sets]
        prompts = [prompt for prompt in prompts if len(re.findall('\[.*?\]', prompt)) == 0]
        if len(prompts) == 0:
            self.log_debug(f"WARN: couldn't infill template: {template}, arguments: {arguments}")
        return prompts
```

## ToolFuzz/src/toolfuzz/correctness/prompt_generation/llm_responses.py

行数：42；SHA-256：`b9998f032b6f84e6cff19302bb4ec6f6aca0ea37d6d8ec27aca89fc8b3907235`。

```python
from typing import List

from pydantic.v1 import BaseModel, Field


class AgentTemplatePrompts(BaseModel):
    template_prompts: List[str] = Field(
        description="List of prompt templates.")


class Argument(BaseModel):
    argument_values: List[str] = Field(description="Synonymous values for an argument.")


class AgentTemplateArguments(BaseModel):
    template_arguments: List[Argument] = Field(
        description="List of the arguments for a tool call. Each Argument is a list of synonymous values for "
                    "that one argument. The order of this list is according to the given template")


class LLMExpectedAnswers(BaseModel):
    expected_answers: List[str] = Field(description="The emulated answers that are returned by tool.")


class LLMExpectedAnswer(BaseModel):
    expected_answer: str = Field(
        description="The one answer which is representing most of the given sentences and is factually correct.")


class PromptSimplification(BaseModel):
    simplified_prompts: List[str] = Field(description="List of simplified prompts.")


class RelevanceResponse(BaseModel):
    is_relevant: bool = Field(description="Whether the model agrees or not that the answer is relevant to the question")
    reason: bool = Field(description="The reason behind the model's choice of the relevancy of the statement")


class CorrectnessResponse(BaseModel):
    correctness_degree: int = Field(
        description="to what extend does the model agree with the statement between 1 and 10. 1 Being the least and 10 meaning exactly what is expected")
    reason: str = Field(description="the reason behind the model's choice of the correctness of the statement")
```

## ToolFuzz/src/toolfuzz/prompt_generation/prompt_generator.py

行数：19；SHA-256：`62552389ee64a0d5fea745fc791b83963646a43f2191b363e2b4d93e929cca03`。

```python
import abc

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI


class PromptGenerator(abc.ABC):

    def __init__(self, tool_extractor, model='gpt-4o', temperature=0):
        self.tool_prompt = tool_extractor.get_tool_docs()
        self.model = ChatOpenAI(model=model, temperature=temperature)

    def _generate_from_template(self, template, template_vars, template_values, return_type):
        parser = JsonOutputParser(pydantic_object=return_type)
        prompt = PromptTemplate(template=template, input_variables=template_vars,
                                partial_variables={"format_instructions": parser.get_format_instructions()})
        chain = prompt | self.model | parser
        return chain.invoke(template_values)
```

## ToolFuzz/src/toolfuzz/tools/info_extractors/tool_wrapper_factory.py

行数：22；SHA-256：`9ebb87137566016e651bbb71d253feccab4b0a019a33b0854ad8290b857fc1b6`。

```python
import inspect

from src.toolfuzz.tools.info_extractors.autogen_tool_wrapper import AutogenToolWrapper
from src.toolfuzz.tools.info_extractors.crew_ai_tool_wrapper import CrewAIToolWrapper
from src.toolfuzz.tools.info_extractors.langchain_tool_wrapper import LangchainToolWrapper
from src.toolfuzz.tools.info_extractors.llama_index_tool_wrapper import LLamaIndexToolWrapper
from src.toolfuzz.tools.info_extractors.tool_wrapper import ToolWrapper


class ToolWrapperFactory:
    @classmethod
    def create_extractor(cls, tool) -> ToolWrapper:
        module = inspect.getmodule(tool).__name__
        if module.startswith('langchain') or module.startswith('composio_langchain'):
            return LangchainToolWrapper(tool)
        elif module.startswith('autogen') or module.startswith('composio_autogen'):
            return AutogenToolWrapper(tool)
        elif module.startswith('llama_index') or module.startswith('composio_llama'):
            return LLamaIndexToolWrapper(tool)
        elif module.startswith('crewai') or module.startswith('composio_crewai'):
            return CrewAIToolWrapper(tool)
        raise NotImplementedError(f"Tool extractor for module {module} is not implemented.")
```

## ToolFuzz/src/toolfuzz/tools/info_extractors/langchain_tool_wrapper.py

行数：124；SHA-256：`c70894400e3d9adab013dd999c9a543c6af97c01c38f343a5e158d047748b0ca`。

```python
import inspect

from src.toolfuzz.tools.info_extractors.dataclasses import ToolArgument, ArgumentType
from src.toolfuzz.tools.info_extractors.tool_wrapper import ToolWrapper


class LangchainToolWrapper(ToolWrapper):

    def _import_environment(self):
        pass

    def invoke_tool(self, kwargs):
        if dir(self.tool).__contains__('invoke'):
            result = self.tool.invoke(kwargs)
        elif dir(self.tool).__contains__('run'):
            result = self.tool.run(**kwargs)
        else:
            raise AttributeError(f"Tool {self.tool} does not have an invoke or run method")
        if isinstance(result, dict) and not result['successful']:
            # Handling composio tools
            raise Exception(result['error'])
        return result

    def get_tool_args(self):
        tool_args = []
        if 'args_schema' in dir(self.tool) and self.tool.args_schema is not None:
            schema = self.tool.args_schema.schema()
            assert 'properties' in schema
            schema = schema['properties']
            for key, value in schema.items():
                tool_args.append(ToolArgument(name=key, type=self.__get_type(value), default=None, has_default=False,
                                              description=value.get('description', None)))
        elif 'func' in self.tool.__dict__:
            schema = inspect.signature(self.tool.func).parameters
            for _, value in schema.items():
                tool_args.append(
                    ToolArgument(name=value.name,
                                 type=ArgumentType(self.__type_to_str(str(value.annotation))),
                                 default=value.default if value.default != inspect._empty else None,
                                 description=self.__get_annotation_doc(value.annotation),
                                 has_default=value.default != inspect._empty))
        else:
            schema = inspect.signature(self.tool._run).parameters
            for _, value in schema.items():
                tool_args.append(
                    ToolArgument(name=value.name,
                                 type=ArgumentType(self.__type_to_str(value.annotation)),
                                 description=self.__get_annotation_doc(value.annotation),
                                 default=value.default if value.default != inspect._empty else None,
                                 has_default=value.default != inspect._empty))

        return tool_args

    @staticmethod
    def __get_annotation_doc(annotation):
        if annotation.__doc__ in [str.__doc__, int.__doc__, dict.__doc__, list.__doc__, bool.__doc__]:
            # Explicit handling for basic types
            return None

        return annotation.__doc__

    def get_tool_src(self):
        if 'source_code' in dir(self.tool):
            return self.tool.source_code
        if 'func' in dir(self.tool) and inspect.isfunction(self.tool.func):
            return inspect.getsource(self.tool.func)
        elif 'func' in dir(self.tool) and inspect.ismethod(self.tool.func):
            return inspect.getsource(type(self.tool.func.__self__))
        try:
            return inspect.getsource(self.tool)
        except:
            return inspect.getsource(type(self.tool))

    def get_tool_name(self):
        return self.tool.name

    def get_tool_declaration_name(self):
        if 'composio_crewai' in inspect.getmodule(self.tool).__name__:
            return self.tool.name
        if 'func' in dir(self.tool) and inspect.isfunction(self.tool.func):
            return self.tool.func.__name__
        elif 'func' in dir(self.tool) and inspect.ismethod(self.tool.func):
            return self.tool.func.__self__.__class__.__name__
        return self.tool.__class__.__name__

    def get_tool_docs(self):
        format = "Tool Name: {name}\nTool Arguments: {args}\nTool Description: {desc}"
        description = self.tool.description
        args = [str(arg) for arg in self.get_tool_args()]
        args = "[" + ", ".join(args) + "]"
        return format.format(name=self.get_tool_name(), args=args, desc=description)

    def __get_type(self, value):
        if type(value) == dict and len(value) == 0:
            # Any types so we can't infer the type - default is string
            return ArgumentType(name='string')
        if 'type' in value:
            if value['type'] == 'object':
                # Dict value is referred to object in schema
                return ArgumentType(name='dict')
            if value['type'] != 'array':
                # If the type is not array, we can return the type directly (it is not container type)
                return ArgumentType(name=value['type'])
            else:
                return ArgumentType(name=value['type'], sub_types=[self.__get_type(value['items'])], has_subtype=True)
        elif 'anyOf' in value:
            subtypes = []
            for item in value['anyOf']:
                subtypes.append(self.__get_type(item))
            return ArgumentType(name='Union', sub_types=subtypes, has_subtype=True)
        raise ValueError(f"Couldn't parse argument type for {value}")

    @staticmethod
    def __type_to_str(type_annotation):
        type_to_str = {
            str: 'string',
            int: 'int',
            dict: 'dict',
            list: 'array',
            bool: 'boolean'
        }
        if type_annotation in type_to_str:
            return type_to_str[type_annotation]
        return str(type_annotation)
```

## ToolFuzz/src/toolfuzz/dataclasses.py

行数：44；SHA-256：`695d68c6721c0a856f297d5abd909494fe6a71c824630b60513f3b218efe5456`。

```python
from typing import List

from pydantic import BaseModel


class Bucket(BaseModel):
    bucket_items: List[str]
    bucket_value: str


class TestResult(BaseModel):
    prompt: str
    tool_arguments: str
    tool_output: str
    agent_output: str
    tool_failure: bool
    unexpected_agent_output: int
    agent_output_not_relevant: bool
    llm_agent_out_reason: str
    trace: str


class PromptSetTestResults(BaseModel):
    tool: str
    template_question: str
    llm_output_expectation: str
    template_prompts: List[str]
    same_arguments_buckets: List[Bucket]
    same_output_buckets: List[Bucket]
    tool_arguments_inconsistency: bool
    tool_output_inconsistency: bool
    individual_run_test_results: List[TestResult]


class TestFailureResult(BaseModel):
    tool: str
    invocation_params: str
    fuzzed_params: str
    expected_exception: str
    exception: str
    prompt: str
    agent_type: str
    trace: str
    successful_trigger: bool
```

## ToolFuzz/src/toolfuzz/utils.py

行数：129；SHA-256：`455f4b2b1224f2f6f5f4f50feeabe5a224e4af97e27a0b0984eac7a21a6516a2`。

```python
import os
import json
import shutil
from collections import defaultdict
from dataclasses import dataclass
from typing import List
from jinja2 import Environment, PackageLoader, select_autoescape

from src.toolfuzz.dataclasses import PromptSetTestResults, TestFailureResult

from importlib import resources

env = Environment(
    loader=PackageLoader("src", 'templates'),
    autoescape=select_autoescape()
)


@dataclass
class TestResultsHtml:
    prompt: str
    tool_arguments: str
    tool_output: str
    agent_output: str
    tool_failure: bool
    unexpected_agent_output: int
    agent_output_not_relevant: bool
    llm_agent_out_reason: str
    trace: str
    input_bucket_color: str
    input_bucket: int
    output_bucket_color: str
    output_bucket: int


@dataclass
class PromptSetTestResultsHtml:
    tool: str
    template_question: str
    llm_output_expectation: str
    individual_run_test_results: List[TestResultsHtml]


TEXT_COLOR_CLASSES = ['primary', 'secondary', 'success', 'danger', 'warning', 'info', 'light', 'dark', 'no_invocation']


def save_test_results_html(test_results, file='./results.html'):
    html_file_content = ''
    full_result_file = '.'.join(file.split('.')[:-1]) + ".json"

    if len(test_results) == 0:
        print("No errors found! Omitting html save.")
        return
    if isinstance(test_results[0], TestFailureResult):
        template = env.get_template('runtime_failure_report.html')
        grouped_res = defaultdict(list)
        for tr in test_results:
            tr.trace = json.dumps(json.loads(tr.trace), indent=4)
            grouped_res[tr.tool].append(tr)
        html_file_content = template.render(test_results=grouped_res, full_result_file=full_result_file)
    elif isinstance(test_results[0], PromptSetTestResults):
        template = env.get_template('correctness_failure_report.html')
        test_results = transform_test_results(test_results)
        grouped_res = defaultdict(list)
        for result in test_results:
            grouped_res[result.tool].append(result)
        html_file_content = template.render(test_results=grouped_res, full_result_file=full_result_file)
    with open(file, 'w') as f:
        f.write(html_file_content)
    with resources.path('src.templates', 'toolfuzz.png') as template_path:
        shutil.copy(template_path, './toolfuzz.png')


def transform_test_results(test_results: List[PromptSetTestResults]) -> List[PromptSetTestResultsHtml]:
    result = []
    for tr in test_results:
        runs = []
        for run in tr.individual_run_test_results:
            arg_color = get_index(tr.same_arguments_buckets, run.tool_arguments)
            out_color = get_index(tr.same_output_buckets, run.tool_output)
            runs.append(TestResultsHtml(prompt=run.prompt,
                                        tool_arguments=run.tool_arguments,
                                        tool_output=run.tool_output,
                                        agent_output=run.agent_output,
                                        tool_failure=run.tool_failure,
                                        unexpected_agent_output=run.unexpected_agent_output,
                                        agent_output_not_relevant=run.agent_output_not_relevant,
                                        llm_agent_out_reason=run.llm_agent_out_reason,
                                        trace=json.dumps(json.loads(run.trace), indent=4),
                                        input_bucket_color=TEXT_COLOR_CLASSES[arg_color],
                                        input_bucket=arg_color,
                                        output_bucket_color=TEXT_COLOR_CLASSES[out_color],
                                        output_bucket=out_color))
        result.append(PromptSetTestResultsHtml(tool=tr.tool, template_question=tr.template_question,
                                               llm_output_expectation=tr.llm_output_expectation,
                                               individual_run_test_results=runs))
    return result


def get_index(buckets, value):
    for idx, item in enumerate(buckets):
        if item.bucket_value == value and value is not None and value != 'None':
            return idx
    return -1


def save_test_results(test_results, file='./results.json'):
    if os.path.exists(file):
        with open(file, 'r') as f:
            content = f.read().strip()
            if len(content) == 0:
                data = []
            else:
                data = json.loads(content)
            data += [tr.model_dump() for tr in test_results]
    else:
        data = [tr.model_dump() for tr in test_results]
    with open(file, 'w') as f:
        f.write(json.dumps(data))


if __name__ == '__main__':
    # Load the results_correctness.json
    import os

    with open('../../correctness_results.json', 'r') as f:
        data = json.load(f)
    test_results = [PromptSetTestResults.model_validate(tr) for tr in data]
    save_test_results_html(test_results)
```

# 第三部分：上游许可证

```text
MIT License

Copyright (c) 2025 SRI Lab, ETH Zurich

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
