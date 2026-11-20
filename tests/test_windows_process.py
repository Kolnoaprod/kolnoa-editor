"""Régression du lancement et du nettoyage des sous-processus."""

import subprocess
import sys

import pytest

from kolnoa_editor.ffmpeg import NO_WINDOW, CancelToken, Cancelled, FFmpegError, run, run_hidden


def test_hidden_process_flags_and_binary_output(monkeypatch):
    captured = []
    original = subprocess.Popen

    def popen(*args, **kwargs):
        captured.append(kwargs["creationflags"])
        return original(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)
    cmd = [sys.executable, "-c", "import sys; sys.stdout.buffer.write(bytes([255]))"]
    assert run(cmd, binary=True).stdout == b"\xff"
    assert run_hidden(cmd, capture_output=True, check=True).stdout == b"\xff"
    assert captured == [NO_WINDOW, NO_WINDOW]
    if sys.platform == "win32":
        assert NO_WINDOW == subprocess.CREATE_NO_WINDOW


@pytest.mark.parametrize("cancelled", [True, False])
def test_process_is_reaped_on_cancel_or_timeout(monkeypatch, cancelled):
    processes = []
    original = subprocess.Popen
    token = CancelToken()

    def popen(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        if cancelled:
            token.cancel()
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)
    with pytest.raises(Cancelled if cancelled else FFmpegError):
        run([sys.executable, "-c", "import time; time.sleep(60)"], cancel=token, timeout=1)
    assert len(processes) == 1 and processes[0].poll() is not None
