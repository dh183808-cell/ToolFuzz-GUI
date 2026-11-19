"""Explicit, single-request checks; importing this module never contacts a service."""

import argparse
from dataclasses import asdict, dataclass
import json
import os

from main.core.model_provider import (
    ModelConfig, ModelConfigurationError, MissingCredentialsError, create_llm,
)


@dataclass(frozen=True)
class ValidationResult:
    check: str
    status: str
    detail: str

    def as_dict(self):
        return asdict(self)


def validate_config(config):
    """Offline readiness only; does not imply connectivity or tool capability."""
    if not isinstance(config, ModelConfig):
        return ValidationResult("config", "invalid_configuration", "ModelConfig is required")
    if not os.getenv(config.api_key_env, "").strip():
        return ValidationResult("config", "missing_credentials", f"Set {config.api_key_env}")
    return ValidationResult("config", "configured", "Configuration complete; API not tested")


def _failure(check, error):
    if isinstance(error, MissingCredentialsError):
        status = "missing_credentials"
    elif isinstance(error, ModelConfigurationError):
        status = "invalid_configuration"
    else:
        status = "request_failed"
    # No response bodies, headers, endpoint strings, or exception inputs.
    return ValidationResult(check, status, f"{type(error).__name__}; check credentials, endpoint, model and parameters")


def probe_text(config):
    """User-triggered text request. This may incur model-service charges."""
    ready = validate_config(config)
    if ready.status != "configured":
        return ValidationResult("text", ready.status, ready.detail)
    try:
        response = create_llm(config).invoke("Reply with OK only.")
        if not response.content:
            return ValidationResult("text", "empty_response", "No text content returned")
        return ValidationResult("text", "passed", "Received text; tool calling not tested")
    except Exception as exc:
        return _failure("text", exc)


PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "toolfuzz_probe",
        "description": "Return the supplied integer; local capability check only.",
        "parameters": {
            "type": "object", "properties": {"value": {"type": "integer"}},
            "required": ["value"], "additionalProperties": False,
        },
    },
}


def probe_tool_call(config):
    """One explicit tool-call request; never executes a returned tool call."""
    ready = validate_config(config)
    if ready.status != "configured":
        return ValidationResult("tools", ready.status, ready.detail)
    try:
        llm = create_llm(config)
        # Some compatible services reject forced choice despite supporting tools.
        # Validate the actual response instead of assuming forced-choice support.
        response = llm.bind_tools([PROBE_TOOL], tool_choice="auto").invoke(
            "Call toolfuzz_probe exactly once with integer value 7."
        )
        calls = response.tool_calls
        if getattr(response, "invalid_tool_calls", None):
            return ValidationResult("tools", "invalid_tool_call", "Malformed tool-call arguments")
        if not calls:
            return ValidationResult("tools", "no_tool_call", "Text response is not a tool call")
        if len(calls) != 1:
            return ValidationResult("tools", "invalid_tool_call", "Expected exactly one tool call")
        call = calls[0]
        if (not isinstance(call.get("id"), str) or not call["id"]
                or call.get("name") != "toolfuzz_probe"
                or call.get("args") != {"value": 7}
                or type(call["args"]["value"]) is not int):
            return ValidationResult("tools", "invalid_tool_call", "Unexpected tool name, ID, or arguments")
        return ValidationResult("tools", "passed", "Received a valid tool call; no external tool executed")
    except Exception as exc:
        return _failure("tools", exc)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url")
    parser.add_argument("--key-env")
    parser.add_argument("--temperature", type=float, default=0)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--max-retries", type=int, default=0)
    parser.add_argument("--check", choices=("config", "text", "tools"), default="config",
                        help="config is offline; text/tools each make a chargeable request")
    args = parser.parse_args(argv)
    try:
        config = ModelConfig(args.provider, args.model, args.base_url, args.key_env,
                             args.temperature, args.timeout, args.max_retries)
    except ModelConfigurationError as exc:
        result = ValidationResult(args.check, "invalid_configuration", str(exc))
    else:
        result = {"config": validate_config, "text": probe_text, "tools": probe_tool_call}[args.check](config)
    print(json.dumps(result.as_dict(), ensure_ascii=False))
    return 0 if result.status in ("passed", "configured") else 1


if __name__ == "__main__":
    raise SystemExit(main())
