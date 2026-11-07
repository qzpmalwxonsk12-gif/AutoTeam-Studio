from pathlib import Path
import time
import uuid

import pytest

from autoteam_studio.core.api import ControllerSettings, TeamController, fake_demo_commands

@pytest.fixture
def tmp_path():
    # Python 3.12's Windows mode=0700 temporary directories exclude the
    # execution sandbox SID. Use an ordinary inherited ACL inside owned paths.
    root = Path(__file__).resolve().parent / ".r" / uuid.uuid4().hex[:6]
    root.mkdir(parents=True, mode=0o777)
    return root

@pytest.fixture
def controller(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "d"))
    instance = TeamController(ControllerSettings(backend="fake", storage_root=tmp_path / "storage"))
    yield instance
    for process in list(instance._workers.values()):
        process.close()

@pytest.fixture
def project(controller, tmp_path):
    return controller.prepare_project(tmp_path / "p space", create=True)

@pytest.fixture
def demo_commands(controller, project):
    commands = fake_demo_commands()
    inspection = controller.inspect_test_authorization(project, commands)
    controller.grant_test_authorization(project, commands, inspection["inspection_id"])
    return commands

def wait_terminal(controller, run_id, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = controller.poll(run_id, 0)
        if response["snapshot"].status in ("completed", "failed", "stopped"):
            return response
        time.sleep(.05)
    raise AssertionError(f"Worker did not reach terminal state: {controller.run_details(run_id)}")
