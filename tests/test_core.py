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
    assert all(cfg.job(j).enabled for j in ("import-replays", "record-replays", "build-dataset", "train-replays"))


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
    assert (cfg.job("train").form, cfg.job("benchmark").form, cfg.job("export").form) == ("train", "model", "model")
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


def _catalogue(path: Path, repo: str = "/src/wizden-ss14", images: int = 10) -> Path:
    (path / "catalogue").mkdir(parents=True)
    (path / "catalogue" / "images.jsonl").write_text("")
    (path / "manifest.json").write_text(json.dumps({"source": {"repo": repo, "commit": "abcdef123456"},
                                                    "generated_at": "2026-10-22T20:33:57Z"}))
    (path / "report.json").write_text(json.dumps({"images": {"written": images}}))
    return path


def _model(path: Path, data: Path, size: str = "tiny", steps: int = 100, rows=()) -> Path:
    (path / "checkpoints").mkdir(parents=True)
    (path / "config.json").write_text(json.dumps({"data": str(data), "size": size, "steps": steps,
                                                  "amp": False, "text_model": "m"}))
    (path / "metrics.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    for r in rows:
        (path / "checkpoints" / f"step-{r['step']:07d}.pt").write_bytes(b"")
    if rows:
        (path / "latest.pt").write_bytes(b"")
    return path


@pytest.fixture
def lib(tmp_path):
    from changeling14_gui.library import Library
    wizden = _catalogue(tmp_path / "ss14-sprite-dataset", images=77366)
    oxyd = _catalogue(tmp_path / "ss14-sprite-dataset-oxyd14", repo="/src/Oxyd14")
    models = tmp_path / "models"
    _model(models / "sprite-pretrain-tiny", wizden,
           rows=[{"step": 0, "unseen_category_top1": 0.01}, {"step": 1000, "unseen_category_top1": 0.12,
                                                             "unseen_text_top5": 0.004},
                 {"step": 1175, "unseen_category_top1": None}])
    cfg = config.parse({"paths": {"sprite_dataset": str(wizden), "models": str(models),
                                  "model_dir": str(models / "sprite-pretrain-tiny")}})
    return Library(cfg, store=tmp_path / "library.json"), wizden, oxyd, models


def test_library_lists_catalogues_and_models(lib, tmp_path):
    library, wizden, oxyd, models = lib
    cats = library.catalogues()
    assert [c.path for c in cats] == [wizden, oxyd]
    assert cats[0].codebase == "wizden-ss14" and cats[0].images == 77366 and cats[0].commit == "abcdef12"
    (model,) = library.models()
    assert model.catalogue == wizden and model.step == 1175 and model.scores["step"] == 1000
    assert [p.name for p in model.checkpoints] == ["step-0000000.pt", "step-0001000.pt", "step-0001175.pt"]
    assert library.new_model_path(wizden, "tiny") == models / "sprite-pretrain-tiny-2"   # taken
    assert library.new_model_path(wizden, "small") == models / "sprite-pretrain-small"
    assert library.new_model_path(oxyd, "small") == models / "sprite-pretrain-small-oxyd14"

    elsewhere = _catalogue(tmp_path / "far" / "away" / "mine")
    library.remember("catalogues", elsewhere)
    library.remember("catalogues", elsewhere)
    assert [c.path for c in library.catalogues()][-1] == elsewhere
    assert json.loads((tmp_path / "library.json").read_text())["catalogues"] == [str(elsewhere)]


def test_train_checks(lib, tmp_path):
    from changeling14_gui import forms
    library, wizden, oxyd, models = lib
    (model,) = library.models()
    assert forms.check_continue(model, "20000", "", False).ok
    assert not forms.check_continue(model, "1000", "", False).ok          # already past it
    assert forms.check_continue(model, "1000", "", True).ok               # start over
    assert not forms.check_continue(model, "x", "", False).ok
    assert not forms.check_continue(model, "20000", "-5", False).ok
    assert forms.check_new(oxyd, str(models / "sprite-pretrain-tiny-oxyd14"), "tiny", "20000", "30").ok
    assert not forms.check_new(oxyd, str(model.path), "tiny", "20000", "").ok   # already a model
    assert not forms.check_new(None, str(models / "new"), "tiny", "20000", "").ok
    assert not forms.check_new(oxyd, str(models / "new"), "huge", "20000", "").ok
    assert forms.train_args("500", " 30 ", True, True, text=False) == [
        "--steps", "500", "--minutes", "30", "--amp", "--fresh", "--no-text"]
    assert forms.train_args("10000", "", False, False, batch=256, optimizer="schedulefree", rich_text=True) == [
        "--steps", "10000", "--batch", "256", "--optimizer", "schedulefree", "--rich-text"]
    assert forms.train_args("100", "", False, False, dedup=False) == ["--steps", "100", "--no-dedup"]
    assert forms.rescale_steps("20000", 128, 256) == "10000"
    assert forms.rescale_steps("10000", 256, 512) == "5000"
    assert forms.rescale_steps("5000", 512, 128) == "20000"
    assert forms.rescale_steps("abc", 128, 256) == "abc"

    assert forms.check_model(model, needs_data=True).ok
    assert model.duplicates_report is None
    (model.path / "duplicates.html").write_text("<html></html>")
    assert library.models()[0].duplicates_report == model.path / "duplicates.html"
    import shutil
    shutil.rmtree(wizden)
    assert not forms.check_continue(library.models()[0], "20000", "", False).ok
    assert not forms.check_model(library.models()[0], needs_data=True).ok
    assert forms.check_model(library.models()[0], needs_data=False).ok   # export needs no catalogue


def test_values_and_extra_args(tmp_path):
    cfg = config.parse({"paths": {"model_dir": "/m/a"}, "values": {"size": "tiny"}})
    job = config.JobSpec("t", "t", ("train", "--out", "{model_dir}", "--size", "{size}"))
    assert config.expand_command(job, cfg, tmp_path) == ["train", "--out", "/m/a", "--size", "tiny"]
    assert config.expand_command(job, cfg, tmp_path, {"model_dir": Path("/m/b"), "size": "small"},
                                 ["--steps", "5"]) == ["train", "--out", "/m/b", "--size", "small", "--steps", "5"]


def test_watchset_cards(tmp_path):
    from changeling14_gui import cards

    def checkpoint(name, step, ranks, meta=None):
        folder = tmp_path / "run" / "watchset" / name
        folder.mkdir(parents=True)
        rows = []
        for i, rank in enumerate(ranks):
            Canvas(4, 4, (0, 0, 0)).save(folder / f"{i:02d}.png")
            rows.append({"image": f"{i:02d}.png", "kind": "name", "name": "security hud",
                         "names": ["security hud"], "group": "Clothing/Eyes", "rank": rank,
                         "guesses": [{"name": "paperwork", "score": 0.2}, {"name": "security hud", "score": 0.1}]})
        write_json_atomic(folder / "predictions.json", {"version": 1, "items": rows})
        write_json_atomic(folder / "meta.json", {"step": step, **(meta or {})})

    checkpoint("step-0000000", 0, [None, None, 2])
    checkpoint("step-0001000", 1000, [1, 2, None], {"unseen_text_top1": 0.02, "unseen_text_top5": 0.1})
    (tmp_path / "run" / "run.json").write_text("{}")
    old = tmp_path / "run" / "watchset" / "old"          # captioned PNGs, no predictions
    old.mkdir()
    Canvas(4, 4, (0, 0, 0)).save(old / "x.png")
    cps = RunFolder(tmp_path / "run").checkpoints()
    first, second, legacy = cps[0], cps[1], cps[2]

    assert legacy.rows == [] and cards.summary(legacy) == ""
    assert cards.card(second.rows[0]).verdict == "✓ Got it"
    assert cards.card(second.rows[1]).verdict == "≈ Close: 2nd guess"
    bad = cards.card(second.rows[2])
    assert bad.title == "Security hud" and bad.verdict == "✗ Thought it was: Paperwork" and bad.colour == cards.BAD
    assert cards.summary(second, first) == ("These are 3 sprites the model has never trained on. It named 1 right "
                                            "and had 1 more in its top 5, up from 0 and 1 at the last checkpoint.")
    assert cards.summary(first).endswith("had 1 more in its top 5.")
    assert cards.summary(second, second) .endswith("no change since the last checkpoint.")
    assert cards.trend(cps) == [(0, 0.0, 1 / 3), (1000, 0.02, 0.1)]


def test_replay_jobs(tmp_path):
    from changeling14_gui import replays
    lib, rec, models = tmp_path / "lib", tmp_path / "rec", tmp_path / "models"
    (lib / "wizards-abc").mkdir(parents=True)
    for name in ("salamander-x-round_115835.zip", "leviathan-y-round_115836.zip"):
        (lib / "wizards-abc" / name).write_bytes(b"zip")
    (lib / "replays.jsonl").write_text("".join(json.dumps({"file": f"wizards-abc/{n}", "build": "wizards-abc",
                                                           "duration": d}) + "\n"
                                               for n, d in (("salamander-x-round_115835.zip", 6270.0),
                                                            ("leviathan-y-round_115836.zip", 4089.0),
                                                            ("gone-round_1.zip", 1.0))))
    (rec / "salamander-x-round_115835" / "frames").mkdir(parents=True)
    for i in range(3):
        (rec / "salamander-x-round_115835" / "frames" / f"{i}.png").write_bytes(b"")
    for name, text in (("sprite-pretrain-nano", True), ("no-text", False)):
        (models / name).mkdir(parents=True)
        (models / name / "config.json").write_text("{}")
        if text:
            (models / name / "text.npz").write_bytes(b"")
    cfg = config.parse({"paths": {"replay_library": str(lib), "replay_recordings": str(rec),
                                  "replay_dataset": str(tmp_path / "ds"), "models": str(models)}})

    items = replays.library_replays(cfg)
    assert [(r.name, r.round_id, r.recorded) for r in items] == [
        ("salamander-x-round_115835.zip", "115835", 3), ("leviathan-y-round_115836.zip", "115836", 0)]
    assert replays.duration_text(6270.0) == "1 h 44 min"
    assert replays.recording_rounds(cfg) == ["115835"]
    assert replays.text_models(cfg) == [models / "sprite-pretrain-nano"]
    (models / "replay-x").mkdir()
    (models / "replay-x" / "config.json").write_text('{"start": "a", "dataset": "b"}')
    (models / "replay-x" / "text.npz").write_bytes(b"")
    assert replays.text_models(cfg) == [models / "sprite-pretrain-nano"]

    src = tmp_path / "ss14"
    (src / "RobustToolbox").mkdir(parents=True)
    (src / ".git").mkdir()
    assert replays.check_record(items[1], str(src), "10", "2", "")[0]
    assert "replaces" in replays.check_record(items[0], str(src), "10", "2", "")[1]
    assert not replays.check_record(items[1], str(tmp_path), "10", "2", "")[0]
    assert not replays.check_record(items[1], str(src), "0", "2", "")[0]
    assert replays.record_args("10", "2", "") == ["--interval", "10", "--players", "2"]
    assert replays.record_args("2.5", "1", "6") == ["--interval", "2.5", "--players", "1", "--max-pictures", "6"]

    assert replays.check_holdout("", ["115835", "115836"])[0]
    assert not replays.check_holdout("115835, 115836", ["115835", "115836"])[0]
    assert not replays.check_holdout("999", ["115835", "115836"])[0]
    assert not replays.check_holdout("", [])[0]
    assert replays.holdout_args(" 115836 ") == ["--holdout", "115836"] and replays.holdout_args("") == []

    start = models / "sprite-pretrain-nano"
    assert not replays.check_replay_train(start, "replay-nano", tmp_path / "ds", "6000", "8", "")[0]   # no dataset
    assert not replays.check_replay_train(start, "replay-nano", None, "6000", "8", "")[0]
    (tmp_path / "ds").mkdir()
    (tmp_path / "ds" / "dataset.json").write_text("{}")
    assert replays.check_replay_train(start, "replay-nano", tmp_path / "ds", "6000", "8", "")[0]
    assert not replays.check_replay_train(start, "sprite-pretrain-nano", tmp_path / "ds", "6000", "8", "")[0]
    assert replays.replay_train_args("6000", "8", "30") == ["--steps", "6000", "--batch", "8", "--minutes", "30"]


def test_overrides_win_over_inbox_and_runs(tmp_path):
    cfg = config.parse({"paths": {"inbox": "/a/inbox"}})
    job = config.JobSpec("x", "x", ("--inbox", "{inbox}"))
    assert config.expand_command(job, cfg, tmp_path, {"inbox": Path("/b/inbox")}) == ["--inbox", "/b/inbox"]


def test_replay_datasets(tmp_path):
    import os
    from changeling14_gui import replays
    legacy, named = tmp_path / "replay-dataset", tmp_path / "replay-datasets"
    cfg = config.parse({"paths": {"replay_dataset": str(legacy), "replay_datasets": str(named)}})
    assert replays.dataset_path(cfg, "replay-dataset") == legacy
    assert replays.dataset_path(cfg, " zoom1 ") == named / "zoom1"
    assert replays.check_dataset_name("bad name/") and replays.check_dataset_name("") and not replays.check_dataset_name("v2.1_x")

    def make(path, frames, mtime):
        path.mkdir(parents=True)
        (path / "dataset.json").write_text('{"version": 1, "tile": 32, "summary": {"frames": %d, "train_frames": 3, '
                                           '"test_frames": 1, "test_rounds": ["115836"]}, "frames": []}' % frames)
        os.utime(path / "dataset.json", (mtime, mtime))
    make(legacy, 1717, 1000)
    make(named / "zoom1", 1600, 2000)
    (named / "empty").mkdir()
    elsewhere = tmp_path / "far" / "mine"
    make(elsewhere, 5, 500)
    found = replays.replay_datasets(cfg, [elsewhere])
    assert [d.path for d in found] == [named / "zoom1", legacy, elsewhere]          # newest first
    assert found[1].describe()[:5] == ("replay-dataset", "1,717", "3", "1", "115836")
    assert replays.check_dataset_out(str(legacy))[1].startswith("Replaces")
    assert replays.check_dataset_out(str(named / "new"))[0]
    (tmp_path / "junk").mkdir()
    (tmp_path / "junk" / "x.txt").write_text("x")
    assert not replays.check_dataset_out(str(tmp_path / "junk"))[0]


def test_failure_reason_and_keep_args(tmp_path):
    from changeling14_gui import forms, replays
    run = RunFolder(tmp_path)
    (tmp_path / "console.log").write_text("detect: starting\n/x/replay-a was trained on the dataset in /d1, not /d2; "
                                          "pass --allow-new-data to resume it anyway\n\n[exit code 1]\n")
    assert run.failure_reason().startswith("/x/replay-a was trained on the dataset")
    (tmp_path / "console.log").write_text("train: x\nerror: something broke\nmore\n[exit code 2]\n")
    assert run.failure_reason() == "error: something broke"
    assert forms.train_args("10", "", False, False, keep="5") == ["--steps", "10", "--keep-checkpoints", "5"]
    assert forms.check_keep("-1") and forms.check_keep("x") and not forms.check_keep("0")
    assert replays.replay_train_args("6000", "8", "", "0", True) == [
        "--steps", "6000", "--batch", "8", "--keep-checkpoints", "0", "--allow-new-data"]


def test_dataset_picker_skips_rebuild_folders(tmp_path):
    from changeling14_gui import replays
    named = tmp_path / "replay-datasets"
    for n in ("audit-v2", ".audit-v2.building", ".audit-v2.previous"):
        (named / n).mkdir(parents=True)
        (named / n / "dataset.json").write_text('{"version": 2, "summary": {"frames": 1}}')
    cfg = config.parse({"paths": {"replay_dataset": str(tmp_path / "replay-dataset"), "replay_datasets": str(named)}})
    assert [d.path.name for d in replays.replay_datasets(cfg)] == ["audit-v2"]


def test_live_overlay_page_helpers(tmp_path):
    from changeling14_gui import overlay_page, replays
    models = tmp_path / "models"
    for name, cfgtext in (("sprite-pretrain-nano", '{"data": "x"}'), ("replay-a", '{"start": "s", "dataset": "d"}')):
        (models / name).mkdir(parents=True)
        (models / name / "config.json").write_text(cfgtext)
        (models / name / "latest.pt").write_bytes(b"")
    (models / "replay-a" / "metrics.jsonl").write_text('{"step": 500, "name_top5": 0.4}\n{"step": 1000, "name_top5": 0.6}\n')
    (models / "replay-a" / "coreml" / "detect_288x320.mlpackage").mkdir(parents=True)
    cfg = config.parse({"paths": {"models": str(models)}})
    assert replays.replay_models(cfg) == [models / "replay-a"]
    (m,) = overlay_page.describe_models(cfg)
    assert (m.step, m.name_top5, m.neural_sizes) == (1000, 0.6, ("288x320",))
    assert m.row() == ("replay-a", "1,000", "60%", "288x320")
    cmd = overlay_page.overlay_command(m.path, "coreml", 0.5, 0.6, 30, "", True, True, False)
    assert cmd[-9:] == ["--backend", "coreml", "--threshold", "0.5", "--name-threshold", "0.6", "--fps", "30",
                        "--floor"]
    assert "--tile-px" in overlay_page.overlay_command(m.path, "torch", 0.5, 0.6, 30, "80", False, False, True)
    s = overlay_page.parse_status('STATUS {"fps":24.5,"ms":{"capture":30,"resize":8,"model":4,"names":5},'
                                  '"engine":"Neural Engine (Core ML)","window":true,"tile_px":80,"tile_source":"grid",'
                                  '"things":60}')
    assert "24.5 fps on Neural Engine" in overlay_page.status_text(s) and "model 4" in overlay_page.status_text(s)
    assert "not found" in overlay_page.status_text({"window": False})
    assert overlay_page.parse_status("overlay: hello") is None
