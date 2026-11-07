"""Pinned Windows build. Output is kept in a new release directory each time."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-tests", action="store_true", help="Use only after a separately recorded passing suite")
    args = parser.parse_args()
    if sys.platform != "win32" or sys.version_info[:2] != (3, 12):
        raise SystemExit("Build on Windows with Python 3.12.")
    root = Path(__file__).resolve().parents[1]
    build_env = os.environ.copy()
    # An older editable install may otherwise win import resolution.
    build_env["PYTHONPATH"] = str(root / "src")
    source_files = [*sorted((root / "src").rglob("*.py")), root / "requirements-build.txt",
                    root / "packaging/AutoTeamStudio.spec", root / "docs/USER_GUIDE.md"]
    source_hashes = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files}
    pins = [line for line in (root / "requirements-build.txt").read_text().splitlines() if line and not line.startswith("#")]
    for pin in pins:
        package, version = pin.split("==")
        if importlib.metadata.version(package) != version:
            raise SystemExit(f"Install the pinned requirements-build.txt first: {pin}")
    if not args.skip_tests:
        subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=root, env=build_env, check=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = root / "dist" / stamp
    output.mkdir(parents=True, exist_ok=False)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", str(output),
                    "--workpath", str(root / "build" / stamp), str(root / "packaging/AutoTeamStudio.spec")], cwd=root, env=build_env, check=True)
    bundle = output / "AutoTeamStudio"
    exe = bundle / "AutoTeamStudio.exe"
    worker = bundle / "worker/AutoTeamStudioWorker.exe"
    if not exe.is_file() or not worker.is_file():
        raise SystemExit("Build did not produce both required executables.")
    smoke = output / "smoke"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PATH"] = os.pathsep.join(entry for entry in env.get("PATH", "").split(os.pathsep)
                                if "python" not in entry.lower() and ".venv" not in entry.lower() and "-venv" not in entry.lower())
    env["QT_QPA_PLATFORM"] = "offscreen"
    subprocess.run([str(exe), "--offline-smoke", str(smoke)], env=env, cwd=output, timeout=240, check=True)
    result = json.loads((smoke / "result.json").read_text())
    if result.get("status") != "passed":
        raise SystemExit(f"Packaged smoke failed: {result}")
    if source_hashes != {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files}:
        raise SystemExit("Source changed during the build. Rebuild before using this release.")
    manifest = {"python": sys.version, "dependencies": pins, "source_sha256": source_hashes, "smoke": result,
                "sha256": {str(p.relative_to(bundle)): hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in sorted(bundle.rglob("*")) if p.is_file()}}
    (output / "build-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (root / "dist/latest-build.txt").write_text(str(exe.relative_to(root)), encoding="utf-8")
    print(f"Verified executable: {exe}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
