# AutoTeam Studio

A Windows desktop application for a shared conversation and an explicitly started development workflow using two separately configured Codex accounts. The interface is built with Python/PySide6 and opens in **Team chat**, rather than treating every message as a coding job.

This repository contains the main application source, tests, packaging scripts and detailed guides. Older A/B backups, coordinator experiments and recovery snapshots are not separate parts that must be installed alongside this checkout.

## What it does

- Shows two account replies in one saved conversation with cancellation/error state.
- Offers **Just chat**, light/dark presentation and saved conversation selection.
- Keeps proposed build briefs separate from starting development.
- Lets the user select a project, review a baseline and authorise test commands.
- Runs the development coordinator only after the user confirms the project/task/test policy.
- Shows work details, corrections/guidance, run history and supported checkpoint recovery.
- Packages a desktop application and a separate worker executable for Windows.

The source includes simulated/fake backends for tests and packaged smoke checks. A passing simulation does not prove that live Codex accounts, CLI versions or quotas behave identically.

## Requirements

- Windows and **Python 3.12**; `pyproject.toml` constrains the runtime to `>=3.12,<3.13`.
- Git for project/baseline/worktree operations.
- PySide6 and development/build dependencies from the project files.
- For real account workflows: independently configured account homes, compatible Codex CLI, and any external editor/project toolchain the selected project needs.

Do not commit account authentication files or copy tokens into project configuration. Real chat/build requests can consume account usage; the app's local counters are not a guarantee of remaining subscription quota.

## Develop from source

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
$env:PYTHONPATH = "$PWD\src"
.\.venv\Scripts\python.exe -m autoteam_studio
```

The explicit source path helps avoid launching an older editable installation from another folder. Launching the UI, browsing history or choosing a folder should not itself start a real model task; sending real messages and confirmed build actions are separate operations.

## Use the app

1. Open Team chat and use **Connect accounts** to inspect account setup.
2. Use **Just chat** when you want conversation without build actions.
3. Review any proposed brief before choosing to build it.
4. Select/create the intended project and review the Git baseline.
5. Save and authorise the project's test commands in Settings.
6. Choose **Build from this message**, confirm the concrete task, then use **Send guidance** during work as needed.
7. Review the result in History and the corresponding project branch/worktree.

Starting a build and integrating/publishing its result are different decisions. Review the security guide before using the app on important existing projects.

## Tests and Windows packaging

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Automated tests are designed around simulated workers rather than real usage-consuming Codex requests. For the documented packaging workflow, use **Build AutoTeam Studio.cmd**, or install `requirements-build.txt` and run:

```powershell
py -3.12 scripts/build_windows.py
```

The build script uses an isolated build environment, tests and a packaged offline simulation. Generated output goes under `dist/<timestamp>/AutoTeamStudio/`; keep the complete package, including worker/internal files, together. **Launch AutoTeam Studio.cmd** can open the locally built latest package, but no prebuilt `dist` executable is shipped in this source snapshot.

## Project structure

| Path | Purpose |
|---|---|
| `src/autoteam_studio/ui/` | Desktop interface and conversation/project views |
| `src/autoteam_studio/core/` | Project, storage, process, conversation and coordination logic |
| `src/autoteam_studio/worker/` | Run worker and stage/gate handling |
| `tests/` | Simulated backend and UI/core checks |
| `scripts/` / `packaging/` | Build and package verification helpers |
| `docs/` | Architecture, use, completion evidence and limits |

## Read next

- [Start here](START_HERE.md)
- [User guide](docs/USER_GUIDE.md)
- [Security and limits](docs/SECURITY_AND_LIMITS.md)
- [Architecture and contracts](docs/ARCHITECTURE_AND_CONTRACTS.md)
- [Conversation-first update](docs/CHAT_UPDATE.md)
- [Historical completion report](docs/COMPLETION_REPORT.md)

Historical test counts describe their original runs. This README update did not exercise real accounts or rebuild a packaged release. Account compatibility, user test commands and a clean-machine install need their own validation. See [BACKUP_NOTES.md](BACKUP_NOTES.md) for omitted local/generated files.
