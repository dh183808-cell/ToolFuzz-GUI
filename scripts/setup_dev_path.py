from pathlib import Path
import site
import sys


# ============================================================
# Resolve workspace paths
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
TOOLFUZZ_ROOT = PROJECT_ROOT / "ToolFuzz"
API_DEPS_ROOT = PROJECT_ROOT / ".venv" / "api-deps"


if not TOOLFUZZ_ROOT.exists():
    raise RuntimeError(
        f"ToolFuzz repository not found: {TOOLFUZZ_ROOT}"
    )


# ============================================================
# Find current virtual environment site-packages
# ============================================================

site_packages_list = site.getsitepackages()

if not site_packages_list:
    raise RuntimeError(
        "Could not locate site-packages."
    )


site_packages = Path(site_packages_list[0]).resolve()


print("Python executable:")
print(sys.executable)

print("\nSite-packages:")
print(site_packages)

print("\nWorkspace root:")
print(PROJECT_ROOT)

print("\nToolFuzz root:")
print(TOOLFUZZ_ROOT)

print("\nAPI dependencies:")
print(API_DEPS_ROOT)

if not API_DEPS_ROOT.is_dir():
    print(
        "Warning: API dependencies directory does not exist. "
        "Its path will still be recorded; the existing workspace paths remain available."
    )


# ============================================================
# Create persistent development path
# ============================================================

pth_file = site_packages / "toolfuzz_workspace.pth"

content = (
    f"{PROJECT_ROOT}\n"
    f"{TOOLFUZZ_ROOT}\n"
    f"{API_DEPS_ROOT}\n"
)

pth_file.write_text(
    content,
    encoding="utf-8",
)


print("\nCreated:")
print(pth_file)

print("\nContents:")
print(content)

print(
    "Development paths installed successfully.\n"
    "Restart Python / VS Code terminal before testing."
)
