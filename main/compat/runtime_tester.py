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
