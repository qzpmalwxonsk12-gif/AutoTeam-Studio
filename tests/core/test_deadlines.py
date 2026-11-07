import sys
import time

import pytest

from autoteam_studio.core.api import ControllerError
from autoteam_studio.core.deadlines import bounded, cleanup_budget
from autoteam_studio.core.process import execute

def test_outer_deadline_caps_nested_subprocess_and_resets_after_failure(tmp_path):
    @bounded(.15)
    def operation():
        @bounded(10)
        def inner():
            return execute([sys.executable, "-c", "import time; time.sleep(60)"], tmp_path, 60)
        return inner()
    started = time.monotonic()
    with pytest.raises(ControllerError) as error:
        operation()
    assert error.value.code == "process_timeout"
    assert time.monotonic() - started < 5
    # A timed-out operation must not poison a later call on the same thread.
    assert execute([sys.executable, "-c", "print('next operation')"], tmp_path, 5)[0] == 0

def test_timeout_still_allows_bounded_failure_evidence(tmp_path):
    from autoteam_studio.core.storage import atomic, read_json
    @bounded(.01)
    def operation():
        time.sleep(.02)
        with cleanup_budget():
            atomic(tmp_path / "failure.json", {"status": "failed"})
    with pytest.raises(ControllerError) as error:
        operation()
    assert error.value.code == "operation_timeout"
    assert read_json(tmp_path / "failure.json") == {"status": "failed"}
