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

The sidebar groups the built-in `jobs.toml` buttons (a job's `group`):

| Section | Button | Runs |
|---|---|---|
| Data | Build sprite catalogue | M0: `changeling14-vision sprites`; opens a window to pick the codebase (SS14 checkouts next to `~/Desktop/SS13-14/wizden-ss14` are listed, or choose any folder) and the output folder. Wizden builds into `ss14-sprite-dataset`, other codebases into `ss14-sprite-dataset-<name>` |
| Data | Import replays | copies replay .zip files from the inbox into the replay library (`changeling14-vision replays import`); deleting them from the inbox is an opt-in checkbox |
| Data | Record replays | pick a library replay and the codebase checkout it came from; plays it in a replay client and saves pictures with answer keys (a game window opens and must stay visible). Recording a replay again asks first |
| Data | Build replay dataset | turns the recordings into a training dataset with optional held-out round ids, in `~/Desktop/SS13-14/replay-datasets/<name>`. The suggested name is always a new one; typing an existing dataset's name rebuilds it after asking |
| Train | Train sprite model | opens a window: continue a model (it keeps the catalogue it was started on) or start a new one on any catalogue, with size, steps, a time limit, half precision and start over (asks first). Resumes from `latest.pt`, saves a checkpoint when stopped |
| Train | Train on replays | pick a sprite model trained with text to start from and a dataset (every folder with a `dataset.json`, newest first, or **Add dataset…**); trains a model to find and name things in replay frames |
| Evaluate and export | Benchmark model | pick a model and checkpoint; scores it on that model's own catalogue and writes `benchmark.md`, per-category accuracy and a mistakes gallery |
| Evaluate and export | Export model | pick a model and checkpoint to export |
| Evaluate and export | Open live overlay | switches to the Live overlay tab |
| Folders | Open replay inbox / models folder / runs folder | opens it in Finder |
| Tools | Run demo training | a fake 60-second run that writes progress, a log and a watch set, to try the app |

Point at a button to see what it does in the line under the sidebar. Heavy
jobs queue behind a running one instead of being greyed out. Cmd-1 and Cmd-2
switch between the Training and Live overlay tabs.

Train, Benchmark and Export need the pipeline's extras:
`pip install -e "$HOME/Desktop/SS13-14/changeling14-vision[train,text,export]"`
(`text` adds sentence-transformers for text matching; MiniLM, about 90 MB, downloads once). Models go to
`~/Desktop/SS13-14/changeling14-models/`.

The pickers list the sprite catalogues (folders with `manifest.json`) beside
`ss14-sprite-dataset` and the models (folders with `config.json`) in
`changeling14-models`, read fresh from disk each time. Folders added with
**Add catalogue…**, **Add model…** or **Add dataset…** are remembered in
`~/.changeling14/library.json`.

Every list has a **Refresh** button (or Cmd-R) as the first button beside
it: it reads the folders again and drops remembered folders that were deleted
or moved, saying how many. The same clean-up runs whenever a window opens. It
only edits the list; files on disk are never deleted. Double-click a row, or
press Return, for the window's main button; Escape cancels.

To change paths or add jobs, copy `src/changeling14_gui/jobs.toml` to
`~/.changeling14/gui.toml` and edit it, or pass `--config FILE`.
`changeling14-gui --list-jobs` shows what's loaded.

Runs go to `~/Desktop/SS13-14/changeling14-runs/<time>-<job>/`. The format
is in [docs/run-folder.md](docs/run-folder.md); the app writes only
`run.json` and `console.log` there.

## Live overlay

The **Live overlay** tab runs a replay-trained model on top of the SS14
window while you play (`changeling14-vision replays overlay`, macOS). Pick a
model, an engine (Neural Engine via Core ML, converted once per game-view
size, or the GPU via PyTorch) and the settings, then Start. The status line
shows fps, milliseconds per stage, the engine and whether the game window was
found. macOS needs Screen Recording for the app that started
`changeling14-gui` (usually Terminal). The overlay only reads the screen.

**Game view** is Auto by default: the overlay works out where the game world
is from SS14's own settings file (read-only: layout, viewport scale, UI scale,
chat size), checks the tile size and zoom against the floor grid and the
player at the centre, snaps its squares to the tile lines every frame and
hides detections under the HUD (menu buttons, actions, chat, alerts, hands).
The status shows the view it found, where from and how sure it is; when it
can't check (a dark view, the lobby) it keeps the last good view. Manual uses
a box you drag: **Re-pick game view (manual)**.

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
