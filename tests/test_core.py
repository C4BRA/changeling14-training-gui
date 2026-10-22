import json
import sys
import time
import zlib
from pathlib import Path

import pytest

from changeling14_gui import codebases, config
from changeling14_gui.png import Canvas
from changeling14_gui.runfolder import RunFolder, list_runs, write_json_atomic
from changeling14_gui.runner import Runner

TOML = """
[paths]
runs = "{runs}"
inbox = "{runs}/inbox"
wizden = "/data/wizden"

[[jobs]]
id = "fast"
label = "Fast demo"
command = ["{{python}}", "-m", "changeling14_gui.demo", "--run-dir", "{{run_dir}}",
           "--seconds", "0.6", "--steps", "6", "--checkpoints", "2", "--frames", "2"]

[[jobs]]
id = "slow"
label = "Slow demo"
command = ["{{python}}", "-m", "changeling14_gui.demo", "--run-dir", "{{run_dir}}", "--seconds", "30"]

[[jobs]]
id = "fails"
label = "Fails"
command = ["{{python}}", "-c", "import sys; print('boom'); sys.exit(3)"]

[[jobs]]
id = "later"
label = "Not yet"
command = []
"""


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "gui.toml"
    path.write_text(TOML.format(runs=tmp_path / "runs"))
    return config.load(str(path))


def wait_for(runner: Runner, predicate, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        runner.poll()
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_builtin_config_loads():
    cfg = config.parse(__import__("tomllib").loads(
        (Path(config.__file__).parent / "jobs.toml").read_text()))
    ids = [j.id for j in cfg.jobs]
    assert ids[:2] == ["demo", "sprites"]
    assert cfg.job("demo").enabled and cfg.job("train").enabled
    assert not cfg.job("import-replays").enabled


def test_placeholders(cfg, tmp_path):
    job = config.JobSpec("x", "x", ("{python}", "--repo", "{wizden}", "--out", "{run_dir}", "{inbox}"))
    cmd = config.expand_command(job, cfg, tmp_path / "r")
    assert cmd == [sys.executable, "--repo", "/data/wizden", "--out", str(tmp_path / "r"),
                   str(tmp_path / "runs" / "inbox")]


def test_overrides_replace_paths_for_one_run(cfg, tmp_path):
    job = config.JobSpec("x", "x", ("--repo", "{wizden}", "--out", "{sprite_dataset}"))
    cfg = config.parse({"paths": {"wizden": "/data/wizden", "sprite_dataset": "/data/ds"}})
    cmd = config.expand_command(job, cfg, tmp_path, {"wizden": Path("/data/oxyd")})
    assert cmd == ["--repo", "/data/oxyd", "--out", "/data/ds"]
    assert cfg.paths["wizden"] == Path("/data/wizden")


def test_builtin_sprites_job_opens_the_picker():
    cfg = config.parse(__import__("tomllib").loads(
        (Path(config.__file__).parent / "jobs.toml").read_text()))
    assert cfg.job("sprites").form == "sprites"
    assert "{wizden}" in cfg.job("sprites").command and "{sprite_dataset}" in cfg.job("sprites").command


def _checkout(path: Path) -> Path:
    for part in codebases.NEEDS:
        (path / part).mkdir(parents=True)
    return path


def test_codebase_picker(tmp_path):
    wizden = _checkout(tmp_path / "wizden-ss14")
    oxyd = _checkout(tmp_path / "Oxyd14")
    (tmp_path / "not-a-checkout" / "Resources" / "Textures").mkdir(parents=True)
    cfg = config.parse({"paths": {"wizden": str(wizden), "sprite_dataset": str(tmp_path / "ss14-sprite-dataset")}})

    assert codebases.find_checkouts(cfg) == [wizden, oxyd]
    assert codebases.checkout_root(oxyd / "Resources" / "Textures") == oxyd
    assert codebases.checkout_root(tmp_path / "not-a-checkout") is None
    assert codebases.default_out(wizden, cfg) == tmp_path / "ss14-sprite-dataset"
    assert codebases.default_out(oxyd, cfg) == tmp_path / "ss14-sprite-dataset-oxyd14"

    assert codebases.check_choice(oxyd, tmp_path / "new").ok
    assert not codebases.check_choice(tmp_path / "not-a-checkout", tmp_path / "new").ok
    assert not codebases.check_choice(oxyd, oxyd / "out").ok
    stray = tmp_path / "stray"
    stray.mkdir()
    (stray / "notes.txt").write_text("x")
    assert not codebases.check_choice(oxyd, stray).ok
    (stray / "manifest.json").write_text("{}")
    assert "rebuilt" in codebases.check_choice(oxyd, stray).message


def test_duplicate_job_ids_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        config.parse({"jobs": [{"id": "a"}, {"id": "a"}]})


def test_demo_run_end_to_end(cfg):
    runner = Runner(cfg)
    run = runner.submit(cfg.job("fast"))
    wait_for(runner, lambda: not runner.active and not runner.pending)
    folder = run.folder
    assert folder.state() == "done"
    info = folder.run_info()
    assert info["exit_code"] == 0 and info["started_at"] <= info["ended_at"]
    status = folder.status()
    assert status.step == status.total == 6 and status.fraction == 1.0
    cps = folder.checkpoints()
    assert [c.step for c in cps] == [3, 6]
    assert len(cps[0].images) == 2
    assert "demo: done" in folder.console_tail()
    assert list_runs(cfg.runs)[0].path == folder.path


def test_heavy_jobs_queue_one_at_a_time_and_stop(cfg):
    runner = Runner(cfg)
    slow = runner.submit(cfg.job("slow"))
    fast = runner.submit(cfg.job("fast"))
    assert slow in runner.active and fast in runner.pending
    wait_for(runner, lambda: slow.folder.state() == "running")
    runner.stop(slow)
    wait_for(runner, lambda: slow not in runner.active, timeout=20)
    assert slow.folder.state() == "stopped"
    wait_for(runner, lambda: not runner.active and not runner.pending)
    assert fast.folder.state() == "done"


def test_cancel_queued_job(cfg):
    runner = Runner(cfg)
    slow = runner.submit(cfg.job("slow"))
    fast = runner.submit(cfg.job("fast"))
    runner.stop(fast)
    assert fast.folder.state() == "cancelled"
    runner.stop(slow)
    wait_for(runner, lambda: not runner.active)


def test_failed_job(cfg):
    runner = Runner(cfg)
    run = runner.submit(cfg.job("fails"))
    wait_for(runner, lambda: not runner.active)
    assert run.folder.state() == "failed"
    assert run.folder.run_info()["exit_code"] == 3
    assert "boom" in run.folder.console_tail()


def test_disabled_job_cannot_start(cfg):
    with pytest.raises(ValueError):
        Runner(cfg).submit(cfg.job("later"))


def test_new_app_session_adopts_running_job(cfg):
    first = Runner(cfg)
    slow = first.submit(cfg.job("slow"))
    wait_for(first, lambda: slow.folder.state() == "running")
    # The app closes and reopens: the job keeps running and still blocks the heavy slot.
    second = Runner(cfg)
    assert second.heavy_busy()
    queued = second.submit(cfg.job("fast"))
    assert queued in second.pending
    second.stop(second.active[0])
    # first.poll() reaps the old supervisor, as init does once the first app has exited.
    wait_for(second, lambda: first.poll() is not None and not second.active and not second.pending)
    assert slow.folder.state() == "stopped"
    assert queued.folder.state() == "done"


def test_status_tolerates_partial_files(tmp_path):
    folder = RunFolder(tmp_path)
    (tmp_path / "status.json").write_text('{"step": 5, "tot')
    assert folder.status().step is None
    write_json_atomic(tmp_path / "status.json", {"step": "7", "total": 10, "stage": "x"})
    assert folder.status().fraction == 0.7
    write_json_atomic(tmp_path / "run.json", {"state": "running", "started_at": time.time() - 70, "pid": 999999})
    assert folder.state() == "lost"
    assert 25 < folder.eta_seconds() < 35


def test_png_writer(tmp_path):
    canvas = Canvas(3, 2, (1, 2, 3))
    canvas.fill_rect(1, 0, 1, 1, (9, 9, 9))
    path = tmp_path / "x.png"
    canvas.save(path)
    data = path.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    idat = data.index(b"IDAT")
    length = int.from_bytes(data[idat - 4:idat], "big")
    raw = zlib.decompress(data[idat + 4:idat + 4 + length])
    assert raw == b"\x00" + bytes([1, 2, 3, 9, 9, 9, 1, 2, 3]) + b"\x00" + bytes([1, 2, 3] * 3)
