#!/usr/bin/env bash
# Builds the Azure Functions source package (outputs/function.zip).
#
# The zip holds only what the Function needs: function_app.py, host.json, requirements.txt,
# pyproject.toml and src/. Dependencies are installed remotely at publish time, so no .venv,
# tests, examples, docs or local configuration ever leaves the machine.
#
# Usage: scripts/package.sh [output.zip]
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output="${1:-$root/outputs/function.zip}"

py=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0)' >/dev/null 2>&1; then
    py="$candidate"
    break
  fi
done
[[ -n "$py" ]] || { echo "Python 3 is required to build the package." >&2; exit 1; }

native() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }

mkdir -p "$(dirname "$output")"
rm -f "$output"

"$py" - "$(native "$root")" "$(native "$output")" <<'PY'
import pathlib
import sys
import zipfile

root, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
files = [root / name for name in ("function_app.py", "host.json", "requirements.txt", "pyproject.toml")]
files += sorted(
    p for p in (root / "src").rglob("*")
    if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
    for file in files:
        if not file.is_file():
            sys.exit(f"Missing file for the package: {file}")
        archive.write(file, file.relative_to(root).as_posix())
print(f"Package: {out} ({out.stat().st_size // 1024} KB, {len(files)} files)", file=sys.stderr)
PY

printf '%s\n' "$output"
