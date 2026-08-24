import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run(command, *, cwd=None, env=None):
    return subprocess.run(
        [str(part) for part in command],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )


def _assert_succeeded(result):
    assert result.returncode == 0, (
        f"command failed with exit code {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def test_installed_console_entry_point_returns_documented_exit_codes(tmp_path):
    build_source = tmp_path / "build-source"
    build_source.mkdir()
    for filename in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy2(ROOT / filename, build_source / filename)
    shutil.copytree(ROOT / "replaycheck", build_source / "replaycheck")

    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    build = _run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-build-isolation",
            "--no-deps",
            "--wheel-dir",
            wheelhouse,
            build_source,
        ]
    )
    _assert_succeeded(build)
    wheels = list(wheelhouse.glob("replaycheck-*.whl"))
    assert len(wheels) == 1

    virtualenv = tmp_path / "venv"
    create_venv = _run([sys.executable, "-m", "venv", virtualenv])
    _assert_succeeded(create_venv)
    scripts = virtualenv / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    replaycheck = scripts / ("replaycheck.exe" if os.name == "nt" else "replaycheck")
    install = _run([python, "-m", "pip", "install", "--no-deps", wheels[0]])
    _assert_succeeded(install)
    assert replaycheck.is_file()

    repository = tmp_path / "consumer-repository"
    repository.mkdir()
    (repository / "adapter.py").write_text(
        """
def safe_handler(event, world):
    transaction = str(event["transaction_id"])
    world.effect("posted", key=transaction, transaction_id=transaction)


def unsafe_handler(event, world):
    transaction = str(event["transaction_id"])
    if world.has("recorded", transaction):
        return
    world.effect("posted", transaction_id=transaction)
    world.effect("recorded", key=transaction, transaction_id=transaction)
""".lstrip(),
        encoding="utf-8",
    )
    fixture = repository / "transactions.jsonl"
    fixture.write_text(
        "\n".join(
            json.dumps({"transaction_id": value, "amount": value * 100})
            for value in (1, 2)
        )
        + "\n",
        encoding="utf-8",
    )

    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["PYTHONNOUSERSITE"] = "1"

    def check(handler, *extra_arguments):
        return _run(
            [
                replaycheck,
                "check",
                "--handler",
                handler,
                "--events",
                fixture.name,
                *extra_arguments,
            ],
            cwd=repository,
            env=environment,
        )

    passing = check("adapter:safe_handler")
    assert passing.returncode == 0, passing.stderr
    assert "PASS" in passing.stdout

    replay_failure = check("adapter:unsafe_handler")
    assert replay_failure.returncode == 1, replay_failure.stderr
    assert "FAIL" in replay_failure.stdout

    invalid_configuration = check(
        "adapter:safe_handler", "--invariant", "adapter:missing"
    )
    assert invalid_configuration.returncode == 2
    assert "replaycheck:" in invalid_configuration.stderr
