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
| Build sprite catalogue | M0: `changeling14-vision sprites` on `~/Desktop/SS13-14/wizden-ss14` |
| Train | sprite pretraining of the Tiny encoder; resumes from `latest.pt`, saves a checkpoint when stopped |
| Benchmark | scores the checkpoint; writes `benchmark.md`, per-category accuracy and a mistakes gallery |
| Export | exports the checkpoint |
| Import replays, Build dataset | greyed out until those steps exist |

Train, Benchmark and Export need the pipeline's extras:
`pip install -e "$HOME/Desktop/SS13-14/changeling14-vision[train,text,export]"`
(`text` adds sentence-transformers for text matching; MiniLM, about 90 MB, downloads once). Models go to
`~/Desktop/SS13-14/changeling14-models/`.

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
