# Project rules

- Store all new project audit reports, code review reports, and engineering quality reports in the outer project's `docs/` directory. Do not create these reports in the project root.
- `docs/` contains local working documents excluded from Git. Preserve its reports even though they are untracked.
- `ToolFuzz/` is the upstream Git submodule. Preserve its source and history; do not update its pinned commit or modify upstream files unless the user explicitly requests it.
- Keep API keys, local environment files, virtual environments, caches, and generated test outputs out of commits. A sanitized `.env.example` may be tracked.
