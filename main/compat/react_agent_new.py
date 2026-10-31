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
