"""One isolated process tree per GUI task; no Streamlit or model imports here."""

import json
from contextlib import redirect_stdout, redirect_stderr
import multiprocessing
import os
from pathlib import Path
import signal
import shutil
import tempfile
from time import monotonic
from time import sleep

from main.core.run_store import redact_credentials, task_directory
from main.core.credentials import credential_scope, protect_existing_loggers, SafeOutput
import sys


def _group_running(pid):
    """Linux zombies have exited; do not wait for init to reap their names."""
    if Path("/proc/self/stat").exists():
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                if int(fields[2]) == pid and fields[0] not in ("Z", "X"):
                    return True
            except (OSError, ValueError, IndexError):
                continue
        return False
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False

def _child(connection, execute, task_id, config, context, staging, temporary_credentials=None):
    # Scope exists only in this spawned interpreter; no environment mutation.
    with credential_scope(temporary_credentials):
        stdout, stderr = SafeOutput(sys.stdout, temporary_credentials), SafeOutput(sys.stderr, temporary_credentials)
        try:
            with protect_existing_loggers(temporary_credentials), redirect_stdout(stdout), redirect_stderr(stderr):
                _execute_child(connection, execute, task_id, config, context, staging)
        finally:
            stdout.finish()
            stderr.finish()
            if temporary_credentials is not None:
                temporary_credentials.clear()


def _execute_child(connection, execute, task_id, config, context, staging):
    try:
        if os.name == "posix":
            os.setsid()
        # Do not execute tools until the parent has installed tree ownership.
        connection.send("ready")
        if connection.recv() != "go":
            return
        for name in ("TMPDIR", "TEMP", "TMP"):
            os.environ[name] = staging
        tempfile.tempdir = staging
        try:
            if execute is None:
                from GUI.services.task_manager import execute_test
                result = execute_test(task_id, config, results_dir=staging, run_context=context)
            else:
                result = execute(task_id, config)
            payload = {"result": result, "error": None}
        except BaseException as exc:
            payload = {"result": None, "error": f"{type(exc).__name__}: {exc}"}
        Path(staging, "response.json").write_text(
            json.dumps(redact_credentials(payload), ensure_ascii=False, allow_nan=False),
            encoding="utf-8",
        )
        connection.send("done")
        # The response is fully read before the parent permits Python shutdown.
        if connection.recv() != "exit":
            raise RuntimeError("Unexpected task shutdown message")
    except (EOFError, BrokenPipeError):
        pass
    finally:
        connection.close()


class _WindowsJob:
    """Kill-on-close Job Object; child waits for assignment before tool code."""
    def __init__(self, pid):
        import ctypes
        from ctypes import wintypes as w

        class Basic(ctypes.Structure):
            _fields_ = [("ProcessTime", ctypes.c_longlong), ("JobTime", ctypes.c_longlong),
                        ("Flags", w.DWORD), ("MinWorkingSet", ctypes.c_size_t),
                        ("MaxWorkingSet", ctypes.c_size_t), ("ActiveProcesses", w.DWORD),
                        ("Affinity", ctypes.c_size_t), ("Priority", w.DWORD),
                        ("Scheduling", w.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

        class Extended(ctypes.Structure):
            _fields_ = [("Basic", Basic), ("IO", IO),
                        ("ProcessMemory", ctypes.c_size_t), ("JobMemory", ctypes.c_size_t),
                        ("PeakProcess", ctypes.c_size_t), ("PeakJob", ctypes.c_size_t)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        kernel.CreateJobObjectW.restype = w.HANDLE
        kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        kernel.OpenProcess.restype = w.HANDLE
        kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel = kernel
        kernel.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
        kernel.QueryInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p]
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        process = None
        try:
            limits = Extended()
            limits.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            process = kernel.OpenProcess(0x0001 | 0x0100, False, pid)
            if not process or not kernel.AssignProcessToJobObject(self.handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.close()
            raise
        finally:
            if process:
                kernel.CloseHandle(process)

    def close(self):
        if self.handle:
            if not self.kernel.CloseHandle(self.handle):
                raise OSError("Could not close the task Job Object")
            self.handle = None

    def active_processes(self):
        import ctypes
        from ctypes import wintypes as w

        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_longlong) for name in
                        ("User", "Kernel", "PeriodUser", "PeriodKernel")] + [
                            (name, w.DWORD) for name in ("Faults", "Total", "Active", "Terminated")]

        info = Accounting()
        if not self.kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return info.Active

    def stop(self):
        import ctypes

        if not self.kernel.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())
        deadline = monotonic() + 5
        while True:
            if self.active_processes() == 0:
                self.close()
                return
            if monotonic() > deadline:
                raise RuntimeError("Task job still has live processes")
            sleep(0.01)


class TaskProcess:
    def __init__(self, task_id, config, *, store_root=None, run_context=None, execute=None, temporary_credentials=None):
        self.directory = task_directory(task_id, root=store_root)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.staging = Path(tempfile.mkdtemp(prefix=".worker-", dir=self.directory))
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe()
        self._child_connection = child
        self.process = context.Process(
            target=_child, args=(child, execute, task_id, config, run_context, str(self.staging), temporary_credentials),
            name=f"toolfuzz-{task_id}",
        )
        self.ready = False
        self.done = False
        self.job = None
        self.started = False
        self.started_at = None
        self.pid = None
        self.stopped = False
        self.response_read = False
        self.exitcode = None
        self.forced = False
        self.shutdown_reason = None
        self.closed = False

    def start(self):
        self.process.start()
        self.started = True
        self.pid = self.process.pid
        self.started_at = monotonic()
        self._child_connection.close()
        if os.name == "nt":
            self.job = _WindowsJob(self.pid)

    def poll(self, cancelled=None):
        if self.connection.poll(0.05):
            try:
                message = self.connection.recv()
            except EOFError:
                raise RuntimeError("Task worker exited without a result") from None
            if message == "ready":
                self.ready = True
                if cancelled is None or not cancelled.is_set():
                    self.connection.send("go")
            elif message == "done":
                self.done = True
                return True
        if not self.process.is_alive():
            raise RuntimeError(f"Task worker exited with code {self.process.exitcode}")
        if not self.ready and monotonic() - self.started_at > 30:
            raise RuntimeError("Task worker did not start within 30 seconds")
        return False

    def response(self):
        payload = json.loads((self.staging / "response.json").read_text(encoding="utf-8"))
        self.response_read = True
        return payload

    def finish(self, cancelled=None, *, timeout=2):
        """Allow normal Python finalizers, falling back to the owned-tree kill.

        Cancellation is checked between short joins, including during shutdown.
        A done/error response is not evidence that the process tree has exited.
        """
        if self.stopped:
            return
        if not self.done or not self.response_read or (cancelled is not None and cancelled.is_set()):
            self.shutdown_reason = "cancelled" if cancelled is not None and cancelled.is_set() else "no_response"
            self.stop()
            return
        try:
            self.connection.send("exit")
        except (EOFError, BrokenPipeError, OSError):
            # It may already have exited; verify it rather than assuming success.
            pass
        deadline = monotonic() + timeout
        while True:
            if cancelled is not None and cancelled.is_set():
                self.shutdown_reason = "cancelled"
                break
            self.process.join(min(0.05, max(0, deadline - monotonic())))
            if not self.process.is_alive():
                self.exitcode = self.process.exitcode
                active = self.job.active_processes() if self.job is not None else (
                    _group_running(self.pid) if os.name == "posix" else False
                )
                if self.exitcode == 0 and not active:
                    if self.job is not None:
                        self.job.close()
                        self.job = None
                    self.shutdown_reason = "natural"
                    self.stopped = True
                    return
                self.shutdown_reason = "remaining_processes" if active else "nonzero_exit"
                break
            if monotonic() >= deadline:
                self.shutdown_reason = "exit_timeout"
                break
        self.stop()

    def stop(self):
        """Stop the owned tree before releasing a slot or touching temporary files."""
        if self.stopped:
            return
        self.forced = self.started
        self.shutdown_reason = self.shutdown_reason or "forced"
        if self.job is not None:
            self.job.stop()
            self.job = None
        if self.started:
            if os.name == "posix":
                try:
                    # setsid runs before the ready handshake and any user code.
                    os.killpg(self.pid, signal.SIGKILL)
                except ProcessLookupError:
                    if self.process.is_alive():
                        self.process.kill()  # Cancel before the ready handshake.
            elif self.process.is_alive():
                self.process.kill()
            self.process.join(5)
            if self.process.is_alive():
                raise RuntimeError("Task worker has not exited; task remains locked")
            self.exitcode = self.process.exitcode
            if os.name == "posix":
                deadline = monotonic() + 5
                while _group_running(self.pid):
                    if monotonic() > deadline:
                        raise RuntimeError("Task process group still has live processes")
                    sleep(0.01)
        self.stopped = True

    def publish(self, result):
        """Only publish finished reports after the entire worker has stopped."""
        result = dict(result)
        artifacts = dict(result.get("artifacts") or {})
        errors = []
        for kind in ("json", "html"):
            value = artifacts.get(f"{kind}_path")
            if not value:
                continue
            try:
                source = Path(value)
                if source.resolve().parent != self.staging.resolve() or source.is_symlink():
                    raise ValueError("Worker report is outside its owned directory")
                if source.is_file():
                    destination = self.directory / source.name
                    os.replace(source, destination)
                    artifacts[f"{kind}_path"] = str(destination)
                else:
                    artifacts[f"{kind}_path"] = None
            except (OSError, ValueError) as exc:
                artifacts[f"{kind}_path"] = None
                errors.append(f"{type(exc).__name__}: {exc}")
        logo = self.staging / "toolfuzz.png"
        try:
            if logo.is_file() and not logo.is_symlink():
                os.replace(logo, self.directory / logo.name)
        except OSError as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
        if errors:
            artifacts["save_error"] = redact_credentials("; ".join(errors))
        result["artifacts"] = artifacts
        return result

    def close(self):
        if self.closed:
            return
        self.stop()
        self.connection.close()
        self._child_connection.close()
        self.process.close()
        # Only this private, validated child folder belongs to this worker.
        if (self.directory.is_symlink() or self.directory.resolve() != self.directory
                or self.staging.is_symlink() or self.staging.resolve().parent != self.directory
                or not self.staging.name.startswith(".worker-")):
            raise ValueError("Refusing cleanup outside the task directory")
        shutil.rmtree(self.staging)
        self.closed = True
