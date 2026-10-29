"""Launching a pane command longer than tmux's single-message command cap.

tmux's client packs a whole ``;``-joined command sequence into one imsg and
rejects it with "command too long" past ~16KB. A claude-native launch carries
the agent's instructions in argv (``--append-system-prompt``), so a large
bundle prompt made every launch of that agent fail. Such commands now run from
a private launch script that ``new-session`` sources.
"""

from __future__ import annotations

import asyncio
import shutil
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import omnigent.inner.terminal as terminal_mod
from omnigent.inner.terminal import TerminalInstance

# tmux 3.5a's effective cap on one packed command message (16384 minus the
# imsg header and argc word); the client fails above it.
_TMUX_COMMAND_CAP_BYTES = 16_364
# Length of ``tmux -S <sock> -f /dev/null``: client options, not packed.
_TMUX_CLIENT_OPTION_ARGS = 5


def _awkward_payload(size: int) -> str:
    """
    Build a prompt-like payload full of shell metacharacters.

    :param size: Minimum payload length in characters, e.g. ``40_000``.
    :returns: Payload with quotes, newlines, ``$``, backticks, backslashes,
        globs and multibyte characters, at least ``size`` characters long.
    """
    unit = 'It\'s "quoted" $HOME `id` \\n \\ * ? [x] ! & ; | é ✓ 🙂\n\tline\n'
    return (unit * (size // len(unit) + 1))[:size]


class _FakeProcess:
    """Stand-in for the tmux client process launched by ``launch``."""

    def __init__(self, returncode: int, stderr: bytes = b"") -> None:
        """
        :param returncode: Exit status to report, e.g. ``0``.
        :param stderr: Stderr bytes to report, e.g. ``b"command too long"``.
        """
        self.returncode = returncode
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        """
        :returns: Empty stdout and the configured stderr.
        """
        return b"", self._stderr


def _patch_tmux_spawn(
    monkeypatch: pytest.MonkeyPatch,
    *,
    returncode: int = 0,
) -> list[tuple[list[str], dict[str, str]]]:
    """
    Replace the tmux spawn in ``launch`` with a recorder.

    :param monkeypatch: Pytest monkeypatch fixture.
    :param returncode: Exit status the fake tmux client reports.
    :returns: List that receives one ``(argv, env)`` pair per spawn.
    """
    calls: list[tuple[list[str], dict[str, str]]] = []

    async def fake_create_subprocess_exec(
        *cmd: str,
        stdout: object,
        stderr: object,
        env: dict[str, str],
    ) -> _FakeProcess:
        """Record the tmux argv and env and return a fake process."""
        del stdout, stderr
        calls.append((list(cmd), dict(env)))
        return _FakeProcess(returncode, b"boom" if returncode else b"")

    monkeypatch.setattr(
        terminal_mod,
        "asyncio",
        SimpleNamespace(
            create_subprocess_exec=fake_create_subprocess_exec,
            subprocess=terminal_mod.asyncio.subprocess,
        ),
    )
    return calls


def _instance(private_dir: Path, args: list[str], **kwargs: object) -> TerminalInstance:
    """
    Build an unlaunched claude-like terminal instance.

    :param private_dir: The instance's private directory.
    :param args: Pane command arguments.
    :param kwargs: Extra :class:`TerminalInstance` fields.
    :returns: The instance.
    """
    return TerminalInstance(
        name="claude",
        session_key="main",
        socket_path=private_dir / "tmux.sock",
        private_dir=private_dir,
        command="claude",
        args=args,
        **kwargs,  # type: ignore[arg-type]
    )


def _pane_command(argv: list[str]) -> str:
    """
    Return the pane command ``new-session`` was given.

    :param argv: Full tmux launch argv.
    :returns: The final ``new-session`` argument.
    """
    start = argv.index("new-session")
    end = argv.index(";", start) if ";" in argv[start:] else len(argv)
    return argv[end - 1]


def _launch_dirs(private_dir: Path) -> list[Path]:
    """
    :param private_dir: The instance's private directory.
    :returns: Per-launch script directories left in it.
    """
    return sorted(private_dir.glob(f"{terminal_mod._LAUNCH_SCRIPT_DIR_PREFIX}*"))


def test_packed_command_bytes_matches_tmux_accounting() -> None:
    """Each argument costs its encoded bytes plus a NUL, separators included."""
    assert terminal_mod._tmux_packed_command_bytes(["a", ";", "é"]) == 2 + 2 + 3
    assert terminal_mod._tmux_packed_command_bytes([]) == 0


@pytest.mark.asyncio
async def test_short_launch_command_stays_inline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ordinary command is still handed to ``new-session`` verbatim."""
    calls = _patch_tmux_spawn(monkeypatch)
    await _instance(tmp_path, ["--model", "opus"]).launch(cwd=tmp_path)

    argv, _ = calls[0]
    assert _pane_command(argv) == "claude --model opus"
    assert _launch_dirs(tmp_path) == []


@pytest.mark.asyncio
async def test_long_launch_command_runs_from_private_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A 40KB ``--append-system-prompt`` launch stays far under tmux's cap.

    The pane command becomes ``. <script>``; the script is private, holds the
    exact quoted argv behind ``exec``, and the tmux env is unchanged.
    """
    calls = _patch_tmux_spawn(monkeypatch)
    prompt = _awkward_payload(40_000)
    long_args = ["--append-system-prompt", prompt, "--model", "opus"]
    await _instance(tmp_path, long_args, keep_alive_after_exit=True).launch(cwd=tmp_path)
    short_dir = tmp_path / "short"
    short_dir.mkdir()
    await _instance(short_dir, ["--model", "opus"], keep_alive_after_exit=True).launch(
        cwd=short_dir
    )

    (argv, env), (_, short_env) = calls
    tmux_command = argv[_TMUX_CLIENT_OPTION_ARGS:]
    packed = terminal_mod._tmux_packed_command_bytes(tmux_command)
    assert packed <= terminal_mod._TMUX_INLINE_LAUNCH_MAX_BYTES < _TMUX_COMMAND_CAP_BYTES
    # The window-scoped hook still follows new-session in the same sequence.
    assert argv[-4:] == ["set-hook", "-w", "pane-died", "detach-client -a"]
    assert env == short_env

    [launch_dir] = _launch_dirs(tmp_path)
    [script] = list(launch_dir.iterdir())
    assert _pane_command(argv) == f". {terminal_mod._shell_quote(str(script))}"
    assert stat.S_IMODE(launch_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(script.stat().st_mode) == 0o600

    remove_line, exec_line, trailing = script.read_bytes().decode().split("\n", 2)
    quote = terminal_mod._shell_quote
    assert remove_line == f"command rm -f -- {quote(str(script))} && command rmdir -- " + quote(
        str(launch_dir)
    )
    quoted = " ".join(terminal_mod._shell_quote(arg) for arg in ["claude", *long_args])
    # The quoted prompt spans lines; everything after the first line is the
    # exec of exactly the argv the inline form would have run.
    assert f"{exec_line}\n{trailing}" == f"exec {quoted}\n"


@pytest.mark.asyncio
async def test_long_launch_script_waits_for_attach_before_exec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``tmux_start_on_attach`` keeps blocking on the attach channel first."""
    _patch_tmux_spawn(monkeypatch)
    instance = _instance(
        tmp_path,
        ["--append-system-prompt", "x" * 20_000],
        tmux_start_on_attach=True,
    )
    await instance.launch(cwd=tmp_path)

    [launch_dir] = _launch_dirs(tmp_path)
    [script] = list(launch_dir.iterdir())
    second_line = script.read_text().split("\n")[1]
    assert second_line.startswith("tmux wait-for omnigent-start-on-attach; exec claude ")


@pytest.mark.asyncio
async def test_inline_threshold_is_inclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sequence exactly at the limit stays inline; one byte over moves out."""
    calls = _patch_tmux_spawn(monkeypatch)
    args = ["--append-system-prompt", "y" * 3_000]
    monkeypatch.setattr(terminal_mod, "_TMUX_INLINE_LAUNCH_MAX_BYTES", 10**9)
    probe_dir = tmp_path / "probe"
    probe_dir.mkdir()
    await _instance(probe_dir, args).launch(cwd=tmp_path)
    exact = terminal_mod._tmux_packed_command_bytes(calls[0][0][_TMUX_CLIENT_OPTION_ARGS:])

    monkeypatch.setattr(terminal_mod, "_TMUX_INLINE_LAUNCH_MAX_BYTES", exact)
    at_limit = tmp_path / "at"
    at_limit.mkdir()
    await _instance(at_limit, args).launch(cwd=tmp_path)
    assert _pane_command(calls[1][0]).startswith("claude --append-system-prompt ")
    assert _launch_dirs(at_limit) == []

    monkeypatch.setattr(terminal_mod, "_TMUX_INLINE_LAUNCH_MAX_BYTES", exact - 1)
    over = tmp_path / "over"
    over.mkdir()
    await _instance(over, args).launch(cwd=tmp_path)
    assert _pane_command(calls[2][0]).startswith(". ")
    assert len(_launch_dirs(over)) == 1


@pytest.mark.asyncio
async def test_failed_launch_discards_launch_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rejected tmux launch leaves no script (or its directory) behind."""
    _patch_tmux_spawn(monkeypatch, returncode=1)
    instance = _instance(tmp_path, ["--append-system-prompt", "z" * 20_000])

    with pytest.raises(RuntimeError, match="tmux launch failed"):
        await instance.launch(cwd=tmp_path)

    assert _launch_dirs(tmp_path) == []
    assert instance.running is False


@pytest.mark.skipif(shutil.which("sh") is None, reason="requires a POSIX shell")
def test_launch_script_round_trips_argv_and_removes_itself(tmp_path: Path) -> None:
    """
    Sourcing the script from ``sh -c`` (what tmux does) passes argv byte-exact.

    Runs the script the way tmux's default shell would and checks the CLI got
    the identical payload, then that the script and its directory are gone.
    """
    payload = _awkward_payload(40_000)
    out = tmp_path / "out.txt"
    argv = ["sh", "-c", 'printf "%s" "$1" > "$2"', "sh", payload, str(out)]
    command_str = " ".join(terminal_mod._shell_quote(arg) for arg in argv)
    script = terminal_mod._write_launch_script(tmp_path, command_str, start_on_attach=False)

    subprocess.run(
        ["sh", "-c", f". {terminal_mod._shell_quote(str(script))}"],
        check=True,
        timeout=30,
    )

    assert out.read_bytes() == payload.encode()
    assert not script.exists()
    assert _launch_dirs(tmp_path) == []


@pytest.mark.skipif(shutil.which("tmux") is None, reason="requires a real tmux binary")
@pytest.mark.asyncio
async def test_long_launch_command_real_tmux(tmp_path: Path, short_tmp_parent: Path) -> None:
    """
    A real tmux launch with a 40KB argument succeeds and runs the exact argv.

    Without the launch script tmux rejects this command ("command too long").
    The pane's process must be the command itself (``exec``), since the
    claude-native status watcher keys on ``#{pane_pid}``.
    """
    payload = _awkward_payload(40_000)
    out = tmp_path / "out.txt"
    pid_file = tmp_path / "pid.txt"
    instance = TerminalInstance(
        name="claude",
        session_key="main",
        # Short socket path: macOS caps AF_UNIX paths at ~103 bytes.
        socket_path=short_tmp_parent / "tmux.sock",
        private_dir=tmp_path,
        command="sh",
        args=[
            "-c",
            'printf "%s" "$1" > "$2.tmp" && mv "$2.tmp" "$2"; echo $$ > "$3.tmp" && '
            'mv "$3.tmp" "$3"; exec sleep 30',
            "sh",
            payload,
            str(out),
            str(pid_file),
        ],
        keep_alive_after_exit=True,
    )
    try:
        await instance.launch(cwd=tmp_path)
        for _ in range(500):
            if out.exists() and pid_file.exists():
                break
            await asyncio.sleep(0.02)
        else:  # pragma: no cover - only on a hang/regression
            raise AssertionError(f"pane command never ran; pane: {await instance.read()}")

        assert out.read_bytes() == payload.encode()
        assert int(pid_file.read_text().strip()) == instance.pane_pid_sync()
        assert _launch_dirs(tmp_path) == []
        assert await instance.is_alive()
    finally:
        await instance.close()
