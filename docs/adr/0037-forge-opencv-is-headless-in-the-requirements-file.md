# ADR 0037 — Forge's OpenCV is made headless in the requirements FILE, not after install

- Status: accepted
- Date: 2026-09-07
- Gated by: L090
- Related: ADR 0036 (assert behaviour, not presence), ADR 0016/0018 (llama.cpp reinstall)

## Context

Forge printed this on every start, in `aio-studio` and in the standalone `sd-forge`:

```
Could not load the Qt platform plugin "xcb" in "" even though it was found.
Aborted
```

The cause is OpenCV. The GUI wheel bundles Qt: `opencv-python==5.0.0.93` ships 29 Qt/xcb
entries including a `cv2/qt/` plugin tree, and the headless wheel of the same version
ships none — measured by listing both wheels, not inferred. In a container with no X
server, loading that tree aborts the process.

What makes it easy to miss is the blast radius. Forge itself survives — the abort kills
whichever subprocess touched `cv2` — so the log continues, supervisord reports RUNNING,
and the QA gate sees a healthy service. Nothing in CI or on the instance says otherwise.

The four Forge variants do not agree about opencv. `classic` pins `opencv-python==4.8.1.78`
and `neo` pins `==5.0.0.93`; `lllyasviel` and `reForge` name no opencv at all and receive it
transitively. Any fix has to cover both shapes.

## The trap that shapes the decision

The obvious fix — uninstall `opencv-python`, install `opencv-python-headless` — is wrong,
and wrong in a way that only shows up at runtime.

`modules/launch_utils.py` decides whether to reinstall like this:

```python
version_installed = importlib.metadata.version(package)   # raises if uninstalled
...
if not requirements_met(requirements_file):
    run_pip(f'install -r "{requirements_file}"', "requirements")
```

`requirements_met()` resolves every pinned name through `importlib.metadata.version()`. An
uninstalled `opencv-python` makes that raise, the function returns False, and Forge
reinstalls the WHOLE requirements file — GUI opencv included — on every container start.
The swap would appear to work in the build, be undone at first boot, and cost a network
install each time. This is the same shape as the llama.cpp reinstall in ADR 0018/0036:
an upstream launcher restoring what we removed because we left its metadata check unhappy.

## Options considered

1. **Rewrite the pin in the requirements file before installing** — chosen. The GUI wheel is
   never fetched, and `requirements_met()` finds `opencv-python-headless` installed, so the
   boot check stays satisfied and nothing is reinstalled. Version is preserved; only the
   distribution name changes. Headless builds exist for every version the variants pin
   (4.8.1.78, 5.0.0.93, 4.11.0.86 — all verified on PyPI).
2. **Uninstall and replace after install** — rejected. Triggers the reinstall above. It also
   downloads the 68MB GUI wheel first, then discards it.
3. **`QT_QPA_PLATFORM=offscreen`** — rejected. Masks the symptom rather than removing the
   cause: the Qt libraries still load, the environment must reach every subprocess to work
   at all, and the image still carries a GUI toolkit it can never use.
4. **Install the X libraries so Qt can load** — rejected. Adds a GUI stack to a headless
   image to satisfy a plugin nothing will ever draw with.
5. **Pass `--skip-install` to Forge** — rejected as the primary fix. It disables the whole
   requirement-checking path, not just this pin, and would hide genuine drift.

Options 1 and 2 are not interchangeable descriptions of one fix; they differ precisely in
whether the boot-time metadata check stays satisfied.

## Decision

Rewrite the pin in the requirements file before installing:

```
sed -i -E 's/^(opencv[-_](contrib[-_])?python)([-_]headless)?==/\1-headless==/' "$forge_req"
```

Then sweep any non-headless opencv that arrived transitively (safe to uninstall precisely
because it is NOT in the requirements file, so nothing at boot resolves its metadata), and
prove the result on the artifact rather than trusting the install lines:

```
python -c "import cv2, pathlib, sys; d = pathlib.Path(cv2.__file__).parent; \
    sys.exit('FATAL: cv2 ships a Qt plugin tree at ...') if (d / 'qt').exists() else ..."
```

A final `grep` fails the build if the requirements file still pins a non-headless opencv —
the guard against the reinstall path reopening silently.

## Consequences

- Both Forge-bearing images are covered. Gated by **L090**, which carries two obligations:
  a headless PIN, and a cv2 assertion on the artifact. Either alone leaves the bug reachable.
- L090 requires `-headless==`, a pin, rather than a mention. The rule's first draft was
  satisfied by its own FATAL string ("still pins a non-headless opencv") and would have
  passed an image that shipped the GUI wheel and merely complained about it.
- **Not reachable by QA.** The abort kills a subprocess, not the service; supervisord reports
  RUNNING and every cell passes. The build-time assertion is the only control, as with the
  GGUF converter in ADR 0036.
- A pre-existing `$WORKSPACE` volume holding an older checkout keeps its old requirements
  file. The fix applies to the image; a stale volume copy is out of its reach.

## What would reverse this

Upstream Forge depending on `opencv-python-headless` directly, or dropping the
`requirements_met()` reinstall path. The first makes the rewrite a no-op; the second would
make option 2 viable, though still wasteful.
