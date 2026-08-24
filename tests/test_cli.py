import json
import os
import subprocess
import sys
from pathlib import Path

from replaycheck.__main__ import _resolve


ROOT = Path(__file__).resolve().parents[1]


def _run_external(tmp_path, adapter_source, *arguments):
    (tmp_path / "adapter.py").write_text(adapter_source)
    fixture = tmp_path / "transactions.jsonl"
    fixture.write_text(
        "\n".join(
            json.dumps({"transaction_id": value, "amount": value * 100})
            for value in (1, 2)
        )
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT), str(tmp_path), environment.get("PYTHONPATH", "")]
    )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "replaycheck",
            "check",
            "--handler",
            "adapter:handle",
            "--events",
            str(fixture),
            *arguments,
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=environment,
    )


def test_external_repository_adapter_passes(tmp_path):
    result = _run_external(
        tmp_path,
        """
def handle(event, world):
    transaction = str(event["transaction_id"])
    world.effect("posted", key=transaction, transaction_id=transaction)
""",
    )
    assert result.returncode == 0, result.stderr
    assert "PASS" in result.stdout


def test_external_repository_adapter_failure_is_a_ci_failure(tmp_path):
    result = _run_external(
        tmp_path,
        """
def handle(event, world):
    transaction = str(event["transaction_id"])
    if world.has("recorded", transaction):
        return
    world.effect("posted", transaction_id=transaction)
    world.effect("recorded", key=transaction, transaction_id=transaction)
""",
    )
    assert result.returncode == 1
    assert "FAIL" in result.stdout


def test_external_repository_adapter_errors_are_configuration_failures(tmp_path):
    result = _run_external(tmp_path, "value = 1\n", "--invariant", "adapter:missing")
    assert result.returncode == 2
    assert "replaycheck:" in result.stderr


def test_console_loader_imports_from_the_invoking_repository(tmp_path, monkeypatch):
    (tmp_path / "local_adapter.py").write_text("def handle(event, world): pass\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys, "path", [entry for entry in sys.path if entry != str(tmp_path)]
    )

    assert callable(_resolve("local_adapter:handle"))
