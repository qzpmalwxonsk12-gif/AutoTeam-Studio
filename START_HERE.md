# AutoTeam Studio

A Windows desktop app for a two-account Codex team. This checkout contains the application, automated tests and a Windows executable build process.

## Launch

Double-click **Launch AutoTeam Studio.cmd** in this folder. It opens the latest verified executable when one has been built. Keep the entire `AutoTeamStudio` release folder together, including `_internal` and `worker`.

For development, with Python 3.12 and the dependencies installed:

```powershell
$env:PYTHONPATH = "$PWD\src"
py -3.12 -m autoteam_studio
```

Setting `PYTHONPATH` matters if an older extracted copy is installed in editable mode. The double-click launcher handles this for you.

## Use the application

1. The app opens in **Team chat**. Click **Connect accounts**, then say hello. Both bots reply in one conversation; no project folder is needed.
2. Turn on **Just chat** for a phone-like group chat with all build actions disabled. Choose **Soft light** or **Quiet dark** to suit your eyes.
3. With Just chat off, describe something to build. The bots can offer a brief for review. Sending a message never starts development automatically.
4. In **Project**, choose or create a folder. Review and save a Git baseline if needed. In **Settings**, save and authorize your test commands.
5. Review the brief or type a task, then click **Build from this message** and confirm the selected project, prompt and tests.
6. Watch the discussion in the same thread. The composer becomes **Send guidance** during work. Expand **Work details** for commands, file activity and token counts.
7. **History** contains results, reports, checkpoint resume and completed runs for revision. Saved conversations have their own dropdown in Team chat.

No model calls occur on launch, folder selection, history browsing or account checks. Sending a real chat message uses both accounts; a confirmed real build starts the development pipeline. Automated tests and packaged smoke runs use simulated workers only.

## Build an executable

Double-click **Build AutoTeam Studio.cmd**. It creates a separate `.build-venv`, installs the pinned build dependencies, runs the tests, builds both executables and runs a packaged offline simulation. Python 3.12 and Git must already be installed. Package installation requires internet access unless wheels are cached.

Command-line equivalent after installing `requirements-build.txt`:

```powershell
py -3.12 scripts/build_windows.py
```

Output: `dist/<build timestamp>/AutoTeamStudio/AutoTeamStudio.exe`. Each build uses a new directory. `dist/latest-build.txt` is updated only after the packaged simulation passes. The adjacent `build-manifest.json` records dependency versions, verification and SHA-256 hashes. `--skip-tests` is for a separately recorded passing source test run; the packaged simulation always runs.

The bundled application and worker do not require Python installed. Git, VS Code, Codex CLI and your project's toolchain remain external dependencies. The executable is not code-signed and has not been tested on a separate clean Windows machine.

See [the user guide](docs/USER_GUIDE.md), [security and limits](docs/SECURITY_AND_LIMITS.md), and [implementation plan](docs/IMPLEMENTATION_PLAN.md).
