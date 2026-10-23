# Changeling14 Training

A small desktop app for running the
[Changeling14 Vision](https://github.com/C4BRA/changeling14-vision) pipeline
without the command line: buttons for each step, a queue, progress and time
left, the log, and the watch-set gallery with a timeline slider.

- **Light.** Tkinter, standard library only. It never loads a model or a
  dataset, polls a few files once a second while something runs, and redraws
  only what changed. About 30–40 MB of memory.
- **Buttons over the same steps.** Each button runs a command from
  `jobs.toml`. Nothing lives only in the app; every step runs the same from a
  terminal.
- **One heavy job at a time.** Heavy jobs queue, since replay playback,
  dataset building and training can't share 8 GB.
- **Jobs outlive the app.** Close the window and running jobs keep going;
  reopen it and it picks them up again.

## Install

Python 3.11 or newer with Tkinter. On a Mac with Homebrew Python, Tkinter
is a separate package:

```sh
brew install python@3.12 python-tk@3.12
```

Use one virtual environment for the app and the pipeline, so the buttons
can run the pipeline steps:

```sh
python3.12 -m venv ~/Desktop/SS13-14/c14-venv
source ~/Desktop/SS13-14/c14-venv/bin/activate
pip install -e ~/Desktop/SS13-14/changeling14-vision
pip install -e ~/Desktop/SS13-14/changeling14-training-gui
changeling14-gui
```

## Jobs

The built-in `jobs.toml` has:

| Button | Runs |
|---|---|
| Demo training run | a fake 60-second run that writes progress, a log and a watch set, to try the app |
| Build sprite catalogue | M0: `changeling14-vision sprites`; opens a window to pick the codebase (SS14 checkouts next to `~/Desktop/SS13-14/wizden-ss14` are listed, or choose any folder) and the output folder. Wizden builds into `ss14-sprite-dataset`, other codebases into `ss14-sprite-dataset-<name>` |
| Train | opens a window: continue a model (it keeps the catalogue it was started on) or start a new one on any catalogue, with size, steps, a time limit, half precision and start over. Resumes from `latest.pt`, saves a checkpoint when stopped |
| Benchmark | pick a model and checkpoint; scores it on that model's own catalogue and writes `benchmark.md`, per-category accuracy and a mistakes gallery |
| Export | pick a model and checkpoint to export |
| Import replays | copies replay .zip files from the inbox into the replay library (`changeling14-vision replays import`); deleting them from the inbox is an opt-in checkbox |
| Record replays | pick a library replay and the codebase checkout it came from; plays it in a replay client and saves pictures with answer keys (a game window opens and must stay visible) |
| Build dataset | turns the recordings into a training dataset in `~/Desktop/SS13-14/replay-dataset`, with optional held-out round ids |
| Train on replays | trains a model to find and name things in replay frames, starting from a sprite model trained with text |

Train, Benchmark and Export need the pipeline's extras:
`pip install -e "$HOME/Desktop/SS13-14/changeling14-vision[train,text,export]"`
(`text` adds sentence-transformers for text matching; MiniLM, about 90 MB, downloads once). Models go to
`~/Desktop/SS13-14/changeling14-models/`.

The pickers list the sprite catalogues (folders with `manifest.json`) beside
`ss14-sprite-dataset` and the models (folders with `config.json`) in
`changeling14-models`, read fresh from disk each time. Folders added with
**Add catalogue…** or **Add model…** are remembered in `~/.changeling14/library.json`.

To change paths or add jobs, copy `src/changeling14_gui/jobs.toml` to
`~/.changeling14/gui.toml` and edit it, or pass `--config FILE`.
`changeling14-gui --list-jobs` shows what's loaded.

Runs go to `~/Desktop/SS13-14/changeling14-runs/<time>-<job>/`. The format
is in [docs/run-folder.md](docs/run-folder.md); the app writes only
`run.json` and `console.log` there.

## Not yet

Per-category accuracy charts, the coverage report, benchmark tables and
temperature come in later milestones, once the pipeline produces them.

## Development

```sh
pip install -e ".[dev]"
pytest
```

## License

MIT.
