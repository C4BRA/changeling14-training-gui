# GUI audit (October 2026)

An audit of the training app (`src/changeling14_gui`) and the
`changeling14-vision` commands it runs. Small, clear bugs were fixed in the
same change; the rest are listed below by severity, with where they are.

## Fixed in this change

| What | Where |
|---|---|
| **Build replay dataset could write where no one looks, or replace a dataset by default.** The default name `replay-dataset` meant the old top-level `~/Desktop/SS13-14/replay-dataset`, which no longer exists (the datasets live in `replay-datasets/`), so a default build made a stray top-level folder; had that folder existed, the default would have replaced it. The old folder is now used only while it still holds a dataset, and the suggested name is always a new one (`replay-dataset-2`, …). | `replays.py` `dataset_path`, `default_dataset_name`; `replay_forms.py` BuildDatasetWindow |
| **Replacing things on disk didn't ask.** Recording a replay again (replaces its pictures), rebuilding an existing dataset, and Start over from step 0 now ask first, with No as the default. | `forms.py` `_Window.confirm`, TrainWindow.ok; `replay_forms.py` RecordReplaysWindow.ok, BuildDatasetWindow.ok |
| **Deleted folders stayed in the remembered lists, with no way to clear them.** Each list has Refresh (Cmd-R), and opening a window also drops remembered folders that are gone. Only `~/.changeling14/library.json` changes. | `library.py` `Library.prune`; `forms.py` `_Window.reload` |
| **Datasets added with Browse… were forgotten when the window closed.** They're remembered now, like models and catalogues. | `replay_forms.py` TrainReplaysWindow.add_dataset |
| **Open folder buttons failed silently** for a missing folder or a failed `open`. They say why now. | `app.py` `open_in_file_browser`, `open_folder` |
| **A live overlay could outlive a crashed app.** It runs in its own session, and only a normal close stopped it; an exit hook stops it too now (not after a force-quit). | `overlay_page.py` start |
| **Secondary text was dark grey (#444–#666)**, hard to read in dark mode. It's one mid grey for both looks. | `forms.py` `MUTED`; `app.py`, `overlay_page.py` |
| **The window's minimum size cut off the sidebar** once it had sections. It's 900×640 now, with a shorter queue list. | `app.py` App.__init__ |
| Inconsistent labels and buttons: see "Buttons" below. | |

## Buttons (consolidated)

- **Sidebar sections:** Data, Train, Evaluate and export, Folders and Tools. Jobs take their section from `group` in `jobs.toml`.
- **Labels start with a verb:** Train sprite model, Benchmark model, Export model, Build replay dataset, Run demo training, Stop or remove job, Open run folder.
- **Folders section:** "Open replay inbox" now sits with new Open models folder and Open runs folder buttons.
- **Open live overlay** switches to that tab (Cmd-2; Cmd-1 goes back).
- **Lists:** every list has the same column of equal-width buttons beside it, in the same order: Refresh, then Add…/Choose…, then extras (Duplicates report, Convert for Neural Engine). Each button has a tooltip.
- **Add buttons:** "Browse…" for a dataset is now **Add dataset…**, like Add model… and Add catalogue…. Browse… stays for typed folder fields.
- **Train on replays:** the start-model list is now a table with size and step, like the other model pickers.
- **Window keys:** in every window, Return or a double-click on a row runs the main button and Escape cancels. On macOS the main button is the rightmost one, as the platform does it.

## Not fixed: bigger items, by severity

### Medium

1. **Keep checkpoints defaults to 5, so older checkpoints are deleted automatically.** The best checkpoint is always kept. You chose this default, and it has a field with a tooltip. Still, it's the one place where the app deletes data without a per-run choice.
   - Options: a default of 0 (keep all), or showing how many checkpoints the run would delete before it starts.
   - Where: `forms.py` TrainWindow (`self.keep`), `replay_forms.py` TrainReplaysWindow (`self.keep`); the deletion itself is in changeling14-vision training.
2. **A job whose supervisor fails to start shows no reason.**
   - The supervisor's output goes to `/dev/null`. If it can't even start (a broken environment, a missing package), the run folder keeps `"queued"` with no log line.
   - The next app start marks it cancelled.
   - Fix: send the supervisor's stderr to `console.log`, and mark the run failed when the process ends without having written "running".
   - Where: `runner.py` `_launch`, `_finished`.
3. **Settings aren't remembered between sessions.**
   - What's lost: window size and tab, the overlay's engine, thresholds, fps cap and view mode, and the last steps, batch and keep values in the Train windows.
   - Every start goes back to the defaults.
   - Fix: a small `~/.changeling14/gui-state.json`.
   - Where: `app.py`, `overlay_page.py`, `forms.py`, `replay_forms.py`.
4. **Queued replay training isn't flagged as busy.**
   - Train sprite model warns when the same model is already queued or running: `busy_models` looks for `--out`.
   - Train on replays names its model with a positional argument and `--models`, so the same replay model can be queued twice without a note.
   - Heavy jobs run one at a time, so nothing breaks, but the second run resumes the first one's output.
   - Where: `app.py` `busy_models`.
5. **Convert for Neural Engine before the first start converts at the wrong size.**
   - With no overlay run yet, it converts at 288×320.
   - The auto game view on his window needs 384×640, which the overlay then converts again on start (about 13 s).
   - Fix: convert at the size the auto view would pick, worked out from the SS14 settings, or drop the default.
   - Where: `overlay_page.py` `convert`.

### Low

6. **No shortcuts for the jobs themselves.** Cmd-1/2 switch tabs and Cmd-R refreshes a picker, but the sidebar buttons have no keys, and the queue list has no Delete-to-stop.
   - Where: `app.py` `_build`.
7. **Exit code 143 always counts as "stopped".** A job killed by someone else's SIGTERM is reported as stopped rather than failed.
   - Where: `supervise.py`.
8. **A model with no `text_model` key counts as having text.**
   - Only matters for very old sprite models.
   - Where: `library.py` `read_model`.
9. **The `replay_dataset` path in `jobs.toml` doesn't exist.** It still points at the old top-level `replay-dataset`.
   - The windows always replace it, so nothing uses it today; a hand-edited job without a window would.
   - Where: `jobs.toml` `[paths]`.
10. **The watch-set gallery always has a dark background**, in the light look too. This is deliberate for pixel art, but it doesn't follow the system look.
    - Where: `app.py` WatchSetView.
11. **Double-click to confirm doesn't apply to two lists:** the codebase list (it already had its own) and the overlay tab's model list (no main button there).

## Checked and fine

- **Opt-in deletion:** Import replays deletes inbox files only when the checkbox is ticked.
- **Rebuilding a dataset** keeps the previous one as `.<name>.previous`.
- **Start over** keeps the saved checkpoints.
- **Closing the app** asks before stopping running jobs; queued ones are cancelled.
- **Jobs outlive the app:** they keep running in their own process group and are picked up again on the next start.
- **No UI freezes from disk scans:** the pickers' scans take about 0.02 s on his library (9 replays, 3 datasets, 2 models). Long work runs as separate programs, and the overlay tab reads its process on a thread.
- **Stopping the overlay** sends SIGINT, then SIGTERM, then SIGKILL to its whole process group. No process was left behind in testing.
