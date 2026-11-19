"""Strict HTTP input models; domain configuration remains in core."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, SecretStr, model_validator

from main.core.model_provider import DEFAULT_MODEL, ModelConfig, ModelRoles, resolve_roles


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class ModelInput(StrictInput):
    provider: str
    model_id: str
    base_url: str | None = None
    api_key_env: str | None = None
    temperature: float = 0
    timeout: float = 60
    max_retries: int = 1

    def to_core(self):
        return ModelConfig(**self.model_dump())

    @model_validator(mode="after")
    def validate_core(self):
        self.to_core()
        return self


class RolesInput(StrictInput):
    prompt: ModelInput
    agent: ModelInput
    judge: ModelInput | None = None

    def to_core(self):
        return ModelRoles(self.prompt.to_core(), self.agent.to_core(),
                          self.judge.to_core() if self.judge else None)


class CredentialsInput(StrictInput):
    prompt: SecretStr | None = None
    agent: SecretStr | None = None
    judge: SecretStr | None = None

    @model_validator(mode="after")
    def nonempty(self):
        for value in (self.prompt, self.agent, self.judge):
            if value is not None:
                secret = value.get_secret_value()
                if not secret.strip() or len(secret) > 8192 or any(ord(char) < 32 for char in secret):
                    raise ValueError("Invalid temporary credential")
        return self

    def to_memory(self):
        from main.core.credentials import TemporaryCredentials
        return TemporaryCredentials({role: value.get_secret_value() for role in ("prompt", "agent", "judge")
                                     if (value := getattr(self, role)) is not None})


class TaskInput(StrictInput):
    test_type: Literal["Runtime", "Correctness"]
    tool_id: str
    iterations: int = 1  # fuzzer_iters for Runtime; prompt_set_iters for Correctness.
    model_name: str = DEFAULT_MODEL
    prompt_temperature: float | None = None
    agent_temperature: float = 0
    judge_temperature: float = 0
    model_roles: RolesInput | None = None
    temporary_credentials: CredentialsInput | None = None

    @model_validator(mode="after")
    def validate_execution(self):
        from main.core.tool_registry import list_tools

        if self.tool_id not in list_tools():
            raise ValueError("Unsupported tool_id")
        if self.iterations < 1:
            raise ValueError("iterations must be positive")
        correctness = self.test_type == "Correctness"
        if not correctness and self.temporary_credentials and self.temporary_credentials.judge is not None:
            raise ValueError("Runtime does not use a Judge credential")
        if self.model_roles:
            legacy = {"model_name", "prompt_temperature", "agent_temperature", "judge_temperature"}
            if legacy & self.model_fields_set:
                raise ValueError("Use model_roles or legacy model parameters, not both")
            roles = self.model_roles.to_core().validate(correctness=correctness)
            if not correctness and roles.judge is not None:
                raise ValueError("Runtime does not use a Judge model")
        else:
            if not correctness and self.judge_temperature != 0:
                raise ValueError("Runtime does not use judge_temperature")
            resolve_roles(model_name=self.model_name, prompt_temperature=self.prompt_value,
                          agent_temperature=self.agent_temperature,
                          judge_temperature=self.judge_temperature, correctness=correctness)
        return self

    @property
    def prompt_value(self):
        return self.prompt_temperature if self.prompt_temperature is not None else (
            0.4 if self.test_type == "Correctness" else 0
        )

    def to_core(self):
        # Do not import GUI/app.py or create any model client.
        from GUI.services.task_manager import RunConfig

        roles = self.model_roles.to_core() if self.model_roles else None
        return RunConfig(
            test_type=self.test_type, tool_id=self.tool_id,
            model_name=roles.agent.model_id if roles else self.model_name,
            iterations=self.iterations,
            prompt_temperature=roles.prompt.temperature if roles else self.prompt_value,
            agent_temperature=roles.agent.temperature if roles else self.agent_temperature,
            judge_temperature=roles.judge.temperature if roles and roles.judge else self.judge_temperature,
            model_roles=roles,
        )
