from pathlib import Path
import site
import sys


# ============================================================
# Resolve workspace paths
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
TOOLFUZZ_ROOT = PROJECT_ROOT / "ToolFuzz"


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


site_packages = Path(site_packages_list[0])


print("Python executable:")
print(sys.executable)

print("\nSite-packages:")
print(site_packages)

print("\nWorkspace root:")
print(PROJECT_ROOT)

print("\nToolFuzz root:")
print(TOOLFUZZ_ROOT)


# ============================================================
# Create persistent development path
# ============================================================

pth_file = site_packages / "toolfuzz_workspace.pth"

content = (
    f"{PROJECT_ROOT}\n"
    f"{TOOLFUZZ_ROOT}\n"
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
