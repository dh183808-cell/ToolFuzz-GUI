"""ChatOpenAI with service-error attribution, including failures caught by ToolFuzz."""

from langchain_openai import ChatOpenAI
from pydantic import PrivateAttr

from main.core.model_provider import ModelCallError, ModelCallState


class ServiceChatOpenAI(ChatOpenAI):
    _call_state: ModelCallState = PrivateAttr(default_factory=ModelCallState)
    _service_config = PrivateAttr(default=None)

    def _service_error(self, error):
        if self._call_state.failure is None:
            # Do not propagate response bodies/URLs/headers which may echo a key.
            status = getattr(error, "status_code", None)
            http = f", HTTP {status}" if isinstance(status, int) else ""
            provider = self._service_config.provider if self._service_config else "compatible"
            self._call_state.failure = ModelCallError(
                f"Model service {provider} failed ({type(error).__name__}{http}); "
                "check credentials, model availability, endpoint, parameters, and timeout"
            )
        return self._call_state.failure

    def _generate(self, *args, **kwargs):
        self._call_state.check()
        try:
            return super()._generate(*args, **kwargs)
        except Exception as exc:
            raise self._service_error(exc) from None

    async def _agenerate(self, *args, **kwargs):
        self._call_state.check()
        try:
            return await super()._agenerate(*args, **kwargs)
        except Exception as exc:
            raise self._service_error(exc) from None

    def _stream(self, *args, **kwargs):
        self._call_state.check()
        try:
            yield from super()._stream(*args, **kwargs)
        except Exception as exc:
            raise self._service_error(exc) from None

    async def _astream(self, *args, **kwargs):
        self._call_state.check()
        try:
            async for chunk in super()._astream(*args, **kwargs):
                yield chunk
        except Exception as exc:
            raise self._service_error(exc) from None
