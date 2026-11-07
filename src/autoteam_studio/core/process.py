"""Deadline-bound argument-array processes, suspended Windows Job containment."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
import time

from .safety import sanitized_environment, sanitize
from .types import ControllerError
from .deadlines import remaining

MAX_LINE = 1024 * 1024

def process_identity(pid: int) -> str | None:
    if os.name != "nt":
        try:
            return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
        except (OSError, IndexError):
            return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return None
        raise ControllerError("recovery_blocked", "Process identity cannot be verified.")
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
            raise ControllerError("recovery_blocked", "Process creation time cannot be verified.")
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise ControllerError("recovery_blocked", "Process liveness cannot be verified.")
        if code.value != 259:
            return None
        return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
    finally:
        kernel.CloseHandle(handle)

def alive(record: dict | None) -> bool:
    if not record:
        return False
    current = process_identity(record["pid"])
    if current is None:
        return False
    if not record.get("identity"):
        raise ControllerError("recovery_blocked", "Recorded process identity is missing.")
    return current == record["identity"]

def own_identity(pid: int | None = None) -> dict:
    pid = os.getpid() if pid is None else pid
    value = process_identity(pid)
    if value is None:
        raise ControllerError("containment_failed", "Cannot establish child process identity.")
    return {"pid": pid, "identity": value}

class Job:
    def __init__(self):
        self.handle = None
        if os.name != "nt":
            return
        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(x, ctypes.c_uint64) for x in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        info = Extended(); info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE, no breakaway
        if not self.handle or not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.close()
            raise ControllerError("containment_failed", "Cannot establish Windows process containment.")

    def assign_and_resume(self, process):
        if os.name == "nt":
            if not self.kernel.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle))):
                raise ControllerError("containment_failed", "Windows refused Job assignment; child was not resumed.")
            native = ctypes.WinDLL("ntdll")
            native.NtResumeProcess.argtypes = [wintypes.HANDLE]
            native.NtResumeProcess.restype = ctypes.c_long
            if native.NtResumeProcess(wintypes.HANDLE(int(process._handle))) != 0:
                raise ControllerError("containment_failed", "Windows refused suspended child resume.")

    def close(self):
        if self.handle:
            self.kernel.TerminateJobObject(self.handle, 1)
            self.kernel.CloseHandle(self.handle)
            self.handle = None

class OwnedProcess:
    def __init__(self, args: list[str], cwd: Path, env: dict | None = None, before_resume=None):
        self.job = Job()
        self.process = None
        try:
            self.process = subprocess.Popen(args, cwd=cwd, env=env or sanitized_environment(), shell=False,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=0x4 | 0x08000000 if os.name == "nt" else 0,
                start_new_session=os.name != "nt")
            if before_resume:
                before_resume(own_identity(self.process.pid))
            self.job.assign_and_resume(self.process)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.process:
            if os.name == "nt":
                self.job.close()
                if self.process.poll() is None:
                    self.process.kill()
            else:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                raise ControllerError("recovery_blocked", "Contained process death could not be confirmed.")
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                if stream:
                    stream.close()
        self.job.close()

def execute(args: list[str], cwd: Path, timeout: float, *, prompt: str = "", env=None,
            on_line=None, on_submitted=None, before_resume=None, output_limit=4 * 1024 * 1024,
            sanitize_output=True, cancel_event=None):
    """Drain both pipes concurrently; reject oversized output rather than hide loss."""
    deadline = time.monotonic() + remaining(timeout)
    owned = OwnedProcess(args, cwd, env, before_resume)
    p = owned.process
    messages = queue.Queue(maxsize=32)
    cancel = threading.Event()
    def put(item):
        while not cancel.is_set():
            try:
                messages.put(item, timeout=.05); return
            except queue.Full:
                pass
    def drain(stream, channel):
        try:
            while True:
                line = stream.readline(MAX_LINE + 1)
                if not line:
                    break
                if len(line) > MAX_LINE:
                    put(("failure", "Raw process record exceeds 1 MiB")); return
                put((channel, line.decode("utf-8", "replace")))
        except (OSError, ValueError):
            pass
        finally:
            put(("end", channel))
    threads = [threading.Thread(target=drain, args=(stream, channel), daemon=True)
               for stream, channel in ((p.stdout, "stdout"), (p.stderr, "stderr"))]
    for thread in threads:
        thread.start()
    submitted = threading.Event()
    submission_error = []
    def submit():
        try:
            p.stdin.write(prompt.encode("utf-8")); p.stdin.close()
            submitted.set()
        except (OSError, ValueError) as exc:
            submission_error.append(exc); submitted.set()
    writer = threading.Thread(target=submit, daemon=True); writer.start()
    outputs = {"stdout": [], "stderr": []}
    size, ended, acknowledged = 0, 0, False
    try:
        while ended < 2 or p.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                raise ControllerError("process_cancelled", "Reply cancelled by you.")
            if time.monotonic() >= deadline:
                raise ControllerError("process_timeout", "Process exceeded its absolute deadline.")
            if submitted.is_set() and not acknowledged:
                if submission_error:
                    raise ControllerError("submission_failed", "Prompt submission could not be established.")
                if on_submitted:
                    on_submitted()
                acknowledged = True
            try:
                channel, line = messages.get(timeout=min(.05, max(.001, deadline - time.monotonic())))
            except queue.Empty:
                continue
            if channel == "end":
                ended += 1; continue
            if channel == "failure":
                raise ControllerError("output_limit", line)
            size += len(line.encode("utf-8"))
            if size > output_limit:
                raise ControllerError("output_limit", "Process output exceeded its bounded evidence allocation.")
            if on_line:
                on_line(channel, line)
            outputs[channel].append(sanitize(line) if sanitize_output else line)
        if not acknowledged:
            writer.join(timeout=max(.001, deadline - time.monotonic()))
            if not submitted.is_set() or submission_error:
                raise ControllerError("submission_failed", "Prompt submission could not be established.")
            if on_submitted:
                on_submitted()
        return p.returncode, "".join(outputs["stdout"]), "".join(outputs["stderr"])
    except ControllerError as exc:
        exc.stdout = sanitize("".join(outputs["stdout"]))[-16000:]
        exc.stderr = sanitize("".join(outputs["stderr"]))[-16000:]
        raise
    finally:
        cancel.set()
        owned.close()
        for thread in threads:
            thread.join(timeout=.2)
