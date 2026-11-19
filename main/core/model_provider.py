"""Serializable OpenAI-compatible model configuration; no requests on import."""

from dataclasses import asdict, dataclass, field
import math
import os
import re
from urllib.parse import urlsplit

from main.core.run_store import redact_credentials
from main.core.credentials import current_credentials


DEFAULT_MODEL = "deepseek-v4-flash"  # Legacy application default, not a provider restriction.
DEFAULT_BASE_URL = "https://api.deepseek.com"
PROVIDERS = {
    "deepseek": (DEFAULT_BASE_URL, "DEEPSEEK_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "siliconflow": ("https://api.siliconflow.cn/v1", "SILICONFLOW_API_KEY"),
    "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY"),
    "custom": (None, None),
}


class ModelConfigurationError(ValueError):
    """Invalid, incomplete, or unsupported model configuration."""


class MissingCredentialsError(RuntimeError):
    """The configured environment variable has no API key."""


class ModelCallError(RuntimeError):
    """A model service failure, distinct from a tool-under-test failure."""


def _base_url(value):
    if not isinstance(value, str) or not value or any(c.isspace() or ord(c) < 32 for c in value):
        raise ModelConfigurationError("base_url must be an HTTP(S) URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port  # Validate malformed/out-of-range ports as well.
        valid = (parsed.scheme in ("http", "https") and parsed.hostname
                 and parsed.netloc and (port is None or port > 0))
    except ValueError:
        raise ModelConfigurationError("base_url is not a valid HTTP(S) URL") from None
    if not valid or "\\" in value or "%" in parsed.netloc:
        raise ModelConfigurationError("base_url is not a valid HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None or "?" in value or "#" in value:
        # Query parameters are not needed for the supported compatible endpoints.
        raise ModelConfigurationError("base_url must not contain userinfo, query parameters, or fragments")
    return value.rstrip("/")


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model_id: str
    base_url: str | None = None
    api_key_env: str | None = None
    temperature: float = 0
    timeout: float = 60
    max_retries: int = 1

    def __post_init__(self):
        if not isinstance(self.provider, str) or self.provider.strip().lower() not in PROVIDERS:
            raise ModelConfigurationError("Unknown provider; use deepseek, openrouter, siliconflow, qwen, or custom")
        provider = self.provider.strip().lower()
        if not isinstance(self.model_id, str) or not self.model_id.strip() or any(
            ord(c) < 32 for c in self.model_id
        ):
            raise ModelConfigurationError("model_id must be a non-empty model identifier")
        url, key_env = PROVIDERS[provider]
        url = self.base_url if self.base_url is not None else url
        key_env = self.api_key_env if self.api_key_env is not None else key_env
        if not url:
            raise ModelConfigurationError("custom requires an explicit base_url")
        if not isinstance(key_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env):
            raise ModelConfigurationError("api_key_env must name an environment variable (required for custom)")
        for name, value in (("temperature", self.temperature), ("timeout", self.timeout)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ModelConfigurationError(f"{name} must be a finite number")
        if not 0 <= self.temperature <= 2:
            raise ModelConfigurationError("temperature must be between 0 and 2")
        if self.timeout <= 0:
            raise ModelConfigurationError("timeout must be positive")
        if isinstance(self.max_retries, bool) or not isinstance(self.max_retries, int) or self.max_retries < 0:
            raise ModelConfigurationError("max_retries must be a non-negative integer")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "model_id", self.model_id.strip())
        object.__setattr__(self, "base_url", _base_url(url))
        object.__setattr__(self, "api_key_env", key_env)

    def summary(self):
        values = asdict(self)
        # This is the variable NAME, never the variable's secret value.
        values["key_env_name"] = values.pop("api_key_env")
        return redact_credentials(values)


@dataclass(frozen=True)
class ModelRoles:
    prompt: ModelConfig
    agent: ModelConfig
    judge: ModelConfig | None = None

    def validate(self, *, correctness=False):
        for role in (("prompt", "agent", "judge") if correctness else ("prompt", "agent")):
            if not isinstance(getattr(self, role), ModelConfig):
                raise ModelConfigurationError(f"{role} requires a ModelConfig")
        if self.judge is not None and not isinstance(self.judge, ModelConfig):
            raise ModelConfigurationError("judge must be a ModelConfig or None")
        return self

    def summary(self):
        return {role: getattr(self, role).summary() if isinstance(getattr(self, role), ModelConfig) else None
                for role in ("prompt", "agent", "judge")}


@dataclass
class ModelCallState:
    """Shared by a run's clients; service failures must not become tool verdicts."""
    failure: ModelCallError | None = field(default=None, repr=False)

    def check(self):
        if self.failure is not None:
            raise self.failure from None


def resolve_roles(model_roles=None, *, model_name=DEFAULT_MODEL, prompt_temperature=0,
                  agent_temperature=0, judge_temperature=0, correctness=False):
    if model_roles is not None:
        if not isinstance(model_roles, ModelRoles):
            raise ModelConfigurationError("model_roles must be a ModelRoles instance")
        return model_roles.validate(correctness=correctness)
    return ModelRoles(
        prompt=ModelConfig("deepseek", model_name, temperature=prompt_temperature),
        agent=ModelConfig("deepseek", model_name, temperature=agent_temperature),
        judge=ModelConfig("deepseek", model_name, temperature=judge_temperature) if correctness else None,
    )


def create_llm(config: ModelConfig, *, api_key=None):
    if not isinstance(config, ModelConfig):
        raise ModelConfigurationError("create_llm requires ModelConfig")
    api_key = api_key if api_key is not None else os.getenv(config.api_key_env)
    if not api_key or not api_key.strip():
        raise MissingCredentialsError(f"{config.api_key_env} is not set.")
    # Lazy loading keeps TaskManager configuration/spawn independent of the SDK.
    from main.core.model_client import ServiceChatOpenAI
    try:
        client = ServiceChatOpenAI(
            model=config.model_id, api_key=api_key, base_url=config.base_url,
            temperature=config.temperature, timeout=config.timeout,
            max_retries=config.max_retries, use_responses_api=False,
        )
        if client.temperature != config.temperature:
            raise ModelConfigurationError("ChatOpenAI adjusted temperature; choose a value supported by this model")
    except ModelConfigurationError:
        raise
    except Exception as exc:
        # SDK/Pydantic messages may echo inputs, including credentials.
        raise ModelConfigurationError(
            f"Model client initialization failed ({type(exc).__name__}); check supported parameters"
        ) from None
    client._service_config = config
    return client


def create_deepseek_llm(model_name=DEFAULT_MODEL, temperature=0):
    """Compatibility entry point: the original signature/defaults remain valid."""
    return create_llm(ModelConfig("deepseek", model_name, temperature=temperature))


def create_role_models(roles, *, correctness=False, legacy=False):
    roles.validate(correctness=correctness)
    names = ("prompt", "agent", "judge") if correctness else ("prompt", "agent")
    credentials = current_credentials()
    # Validate all required credentials before constructing any real clients.
    # Legacy roles share one credential and use the public compatibility entry point.
    if not legacy or credentials:
        for name in names:
            config = getattr(roles, name)
            supplied = credentials.get(name) if credentials else None
            if not (supplied or os.getenv(config.api_key_env, "")).strip():
                raise MissingCredentialsError(f"{name}: {config.api_key_env} is not set.")
    state = ModelCallState()
    models = {}
    for name in names:
        config = getattr(roles, name)
        supplied = credentials.get(name) if credentials else None
        model = (create_llm(config, api_key=supplied) if supplied else
                 create_deepseek_llm(config.model_id, config.temperature) if legacy else create_llm(config))
        if hasattr(model, "_call_state"):
            model._call_state = state
        models[name] = model
    return models


def check_model_calls(models):
    for model in models.values():
        state = getattr(model, "_call_state", None)
        if isinstance(state, ModelCallState):
            state.check()
