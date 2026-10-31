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
