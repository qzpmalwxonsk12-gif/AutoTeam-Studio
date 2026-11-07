"""Development entry point and PyInstaller worker entry point."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--fake-chat":
        from autoteam_studio.worker.fake_chat import main as chat_main
        return chat_main()
    if argv and argv[0] in ("--fake-cli", "--fake-test"):
        from autoteam_studio.worker.fake_cli import main as fake_main
        return fake_main(["--test"] if argv[0] == "--fake-test" else argv[1:])
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--protocol", type=int, required=True)
    args = parser.parse_args(argv)
    from autoteam_studio.worker.engine import Worker
    from autoteam_studio.core.types import ControllerError
    try:
        Worker(args.run_dir.resolve(strict=True), args.token, args.protocol).execute()
    except ControllerError as error:
        print(error.message, file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
