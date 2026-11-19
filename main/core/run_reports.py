"""Use upstream report templates while keeping every artifact in its run folder."""

from collections import defaultdict
from importlib import resources
import json
from pathlib import Path

from main.core.run_store import redact_credentials


def save_reports(results, output_path, *, clear_previous=True, secret_env_names=()):
    # Lazy imports keep the store and task manager usable without model packages.
    from src.toolfuzz.utils import env, transform_test_results

    def redact(value):
        return redact_credentials(value, secret_env_names=secret_env_names)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    json_path = Path(str(output_path) + ".json")
    html_path = Path(str(output_path) + ".html")
    data = [redact(item.model_dump()) for item in results]
    if not clear_previous and json_path.exists():
        data = redact(json.loads(json_path.read_text(encoding="utf-8"))) + data
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    if results:
        # Upstream's save() copies its logo to the process working directory.
        # Reuse its templates directly so the logo goes beside this report.
        safe_results = [type(item).model_validate(redact(item.model_dump())) for item in results]
        runtime = "successful_trigger" in safe_results[0].model_dump()
        if not runtime:
            safe_results = transform_test_results(safe_results)
        grouped = defaultdict(list)
        for item in safe_results:
            grouped[item.tool].append(item)
        template = "runtime_failure_report.html" if runtime else "correctness_failure_report.html"
        html = env.get_template(template).render(
            test_results=grouped, full_result_file=json_path.name,
        )
        # One link in the upstream correctness template still uses a fixed name.
        html = html.replace('href="./results.json"', f'href="{json_path.name}"')
        logo = resources.files("src.templates").joinpath("toolfuzz.png")
        (output_path.parent / "toolfuzz.png").write_bytes(logo.read_bytes())
    else:
        html = '<!doctype html><html><meta charset="utf-8"><title>ToolFuzz</title><body><p>No test results were recorded.</p></body></html>'
    html_path.write_text(redact(html), encoding="utf-8")
