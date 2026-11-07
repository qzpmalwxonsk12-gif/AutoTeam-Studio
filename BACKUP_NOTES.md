# Source snapshot

Use the main patch_2 workspace. Do not publish A/B backups, clean/intermediate/build-pack snapshots, archived debug files, early coordinator variants, or recovery helpers as separate repositories.

Original local source files are unchanged. This private backup excludes credentials, signing keys, local configuration, dependencies, build caches, runtime data, and unrelated research/old backup folders. No build or functionality test was run as part of this upload.

## Excluded paths

- `AutoTeam-Studio/.build-venv/`
- `AutoTeam-Studio/.pytest_cache/`
- `AutoTeam-Studio/.qodo/`
- `AutoTeam-Studio/artifacts/`
- `AutoTeam-Studio/build/`
- `AutoTeam-Studio/dist/`
- `AutoTeam-Studio/scripts\__pycache__/`
- `AutoTeam-Studio/src\autoteam_studio\__pycache__/`
- `AutoTeam-Studio/src\autoteam_studio\core\__pycache__/`
- `AutoTeam-Studio/src\autoteam_studio\ui\__pycache__/`
- `AutoTeam-Studio/src\autoteam_studio\worker\__pycache__/`
- `AutoTeam-Studio/tests\__pycache__/`
- `AutoTeam-Studio/tests\core\.r/`
- `AutoTeam-Studio/tests\core\__pycache__/`
- `AutoTeam-Studio/tests\ui\__pycache__/`