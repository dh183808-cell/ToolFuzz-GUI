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


try:
    from ddgs.exceptions import DDGSException
except ImportError:
    # Correctness tests for other tools do not require the search dependency.
    SEARCH_ERRORS = ()
else:
    SEARCH_ERRORS = (DDGSException,)


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

    def run_prompt(self, prompt):
        try:
            # The upstream method resets context in finally, including when a
            # DDGS error escapes. Do not reset it a second time here.
            return super().run_prompt(prompt)
        except SEARCH_ERRORS as exc:
            error = f"{type(exc).__name__}: {exc}"
            return None, TestResult(
                prompt=prompt,
                tool_arguments="Unavailable: tool invocation was interrupted",
                tool_output="Tool FAILURE",
                agent_output=f"Search tool failed: {error}",
                tool_failure=True,
                # Preserve the upstream failure-record schema. This is not a
                # judge score: the reason/trace explicitly mark evaluation skipped.
                unexpected_agent_output=0,
                agent_output_not_relevant=True,
                llm_agent_out_reason=(
                    f"Correctness was not evaluated because the search tool failed: {error}"
                ),
                trace=json.dumps(
                    {
                        "tool_error": str(exc),
                        "error_type": f"{type(exc).__module__}.{type(exc).__name__}",
                        "evaluation_skipped": True,
                    },
                    ensure_ascii=False,
                ),
            )

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

                if outs:
                    (
                        tool_output_inconsistency,
                        output_buckets,
                    ) = oracle.evaluate_tool_output(outs)
                    (
                        tool_arguments_inconsistency,
                        args_buckets,
                    ) = oracle.evaluate_tool_arguments(outs)
                else:
                    # No successful calls means there is nothing to compare.
                    # Do not report an empty comparison as an inconsistency.
                    tool_output_inconsistency = False
                    tool_arguments_inconsistency = False
                    output_buckets = []
                    args_buckets = []

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
