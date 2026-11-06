# ADR 0062 — The portal changes a container only on its first boot, and a release reaches the
fleet only after QA

- **Status:** Proposed
- **Date:** 2026-11-03
- **Decision owner:** Rob Ballantyne
- **Design review:** several rounds, most recently on 2026-11-05. The last round asked for
  changes; they are addressed in this text but were not re-reviewed. By owner decision, the
  remaining check is the build and the live QA matrix on a branch, not a further review round.
- **Amends:** [ADR 0038](0038-serverless-mode-is-declared-by-the-platform.md) Decision 1: on new
  images, serverless workers no longer skip the first-boot portal update (Decision 4).
  [ADR 0061](0061-go-listeners-are-plain-tcp.md): `mptcp_off` runs once per portal install under
  the harness, rather than on every boot.
- **Supersedes:** the trigger in [ADR 0015](0015-portal-release-automation.md) (a merge that bumps
  `portal-aio/VERSION` publishes straight to latest). ADR 0015's other guards stay.

## Context

### The channel
- **It reaches containers a rebuild can't.** Users launch older image tags and saved templates,
  and saved templates never re-pin, so a rebuild and promote never reach them. The first-boot
  portal update does.
- **It has already been used.** ADR 0061 used it: portal v3.1.10 made Caddy and Syncthing listen as
  plain TCP on every older image that boots it.
- **The other route is narrower.** Each image's baked `HOTFIX_SCRIPT` stage (since 2025-06) is the
  only other route, and it reaches only templates we control.
- **The goal.** Make this a deliberate, repeatable way to deliver changed binaries and configs,
  without a full rebuild.

How the update behaves on older images:
- **When it runs:** on first boot only. Since 2025-03, `/.first_boot_complete` is written during
  the first boot, before supervisord starts any service.
- **Which containers skip it:**
  - On every vintage, an explicit `PORTAL_VERSION` pin installs that version instead of latest,
    even when it is lower. New images change this: they skip a pin below the baked version
    (Decision 4).
  - Since 2025-08, serverless workers and the `--no-update-portal` boot flag skip it. The flag goes
    on the `entrypoint.sh` line in the onstart script in the platform's SSH and Jupyter launch
    modes, and in the Docker arguments in entrypoint mode. There is no environment-variable
    equivalent on any existing vintage. New images add one (Decision 4).
  - Since 2026-03, a container whose installed version already equals the target skips the
    download.
  - The 2025-03 vintage honours the pin only. Its boot script warns `Unknown flag:
    --no-update-portal` and updates anyway, on every first boot, serverless included (verified
    in entrypoint mode on 2026-11-05).
- **What the updater does:** it is the copy baked into the image, and it cannot be changed. It
  looks up the release GitHub calls latest, with an unauthenticated API call that is not retried.
  It downloads one asset, `instance-portal.tar.gz`, unpacks it over `/opt` as root, and runs
  `pip install -r` into the portal venv. Since 2026-03 it writes `portal-aio/VERSION` and
  updates on any version mismatch, upgrade or downgrade. Earlier updaters write
  `PORTAL_VERSION` to `/etc/environment` instead, and update on every first boot whatever the
  versions.
- **What we control:** only the tarball's contents. ADR 0015 binds it to a `git archive` of
  `portal-aio/`. Anything new must arrive in that tarball and be switched on by portal code that
  baked scripts already run. In practice that is `caddy_config_manager.main()`, run by the baked
  `caddy.sh` from supervisor.
- **What the updater never does:** retry, report, or delete files. A failed lookup skips the update
  for the life of the container. Files dropped from a later tarball stay on disk.
- **Python:**
  - **2025-03 images** built the portal venv with the system `python3.10 -m venv` and have no
    `uv`.
  - **Images since 2025-06** build it with `uv venv -p 3.11`.
  - **Order of operations on every vintage.** The frozen updater runs `pip install -r` into
    whatever venv the image has. Later, the baked `caddy.sh` runs
    `/opt/portal-aio/venv/bin/python caddy_config_manager.py`. So the first portal code to run
    after an update runs on the image's own Python.

### What the channel can reach on images in the field
The only hook is portal code that a baked script runs. On existing images, the first such code is
`caddy_config_manager.py`, which supervisord starts through `caddy.sh` at boot stage 65.

| Part of the boot | Reachable from a release on existing images? |
|---|---|
| The platform's side: container creation, injected environment and ports, the platform-signed `/etc/instance.crt` | Never. It is not our code |
| Launch-mode `/.launch`: the openssh install, sshd, `authorized_keys`, the first Jupyter process, `onstart.sh` | Never directly. Files it calls, such as our `jupyter` shim, can be changed by harness code, and take effect from their next use |
| The frozen updater itself | Never. It changes only in new images |
| `entrypoint.sh`, `boot_default.sh`, stages 05 to 15, and stage 25 (the update) | Not on first boot, because they run before the hook. Files they read can be changed for later boots |
| Stages 35 to 55: syncs, bashrc, SSH keys, venv backup, TLS certificate generation | Not on first boot. They run after the update but before the hook |
| supervisord's configuration | On first boot, through a supervisor reread (Decision 3) |
| Programs that wait for `/etc/portal.yaml`: instance portal, tunnel manager, Jupyter in entrypoint mode, Syncthing, TensorBoard, and derivative apps that use the same wait | On first boot. They start after the early slot |
| Programs that do not wait (cron, pyworker), and stages 70 to 95, which run alongside `caddy.sh` | Not reliably on first boot. A change takes effect at their next start, or through a supervisor restart |
| Environment the boot shell exports (stages 05, 10, 12) | Since 2025-08, every supervisor script sources `/etc/environment` when it starts, so a value written there reaches a program through a named restart on first boot. Before 2025-08, programs inherit supervisord's own environment, and only a later boot changes it |
| Serverless workers | Not on images built since 2025-08: the boot skips the update for them (in stage 25 since 2026-09), and `caddy.sh` exits on them. The 2025-03 vintage takes the update and reaches the hook like any other container |
| `BOOT_SCRIPT` custom boots | Only if the custom script runs the update and supervisor |

An earlier hook was looked for and ruled out. `pip install -r` at stage 25 would run build code
for a local source package named in `requirements.txt`. A local path cannot carry a hash, though,
and a hidden hook of that kind is not something to build on.

So on existing images, first-boot behaviour before stage 65 is fixed by rebuilding images, or in
an extreme case by a first-boot reboot (Decision 3), not by a release alone. New images move the
hook to the start of the boot and bring serverless inside the channel (Decision 4).

### What review found
- **Broken containers stay broken.** A container updates once, at first boot, and no later release
  reaches it. Marking a release as a prerelease, or publishing a higher version, only changes what
  future first boots receive. A fix that runs but behaves wrongly stays broken on every container
  that took it. Failing open catches exceptions, not wrong behaviour.
- **Merging is shipping.** Under ADR 0015 a merge goes fleet-wide with no pause, no QA and no
  canary. That was acceptable for portal code that writes a Caddyfile. It is not acceptable for a
  channel meant to replace binaries and configs as root.
- **The early hook delays every service, and must never stop config generation.** Code at the
  top of `main()` delays `/etc/portal.yaml`, and Caddy itself starts only after `main()` returns.
  - Supervised services wait for that file. On images before 2026-08 they wait forever. Since
    2026-08 they exit after 120 seconds and supervisord restarts them, so they wait again. No
    vintage has a hard deadline: slow work there delays every app for as long as it runs.
  - On images before 2026-08, `caddy.sh` touches `/etc/portal.yaml` even when the config manager
    failed. Every waiting program, derivative apps included, then finds an empty file and skips
    itself. `/etc` survives restarts, so that container never starts its apps again
    (`docs/invariants.md`).
  - So downloads and builds cannot run there, and nothing there may stop the config manager.
- **Signing cannot protect older images.** Their updater verifies nothing, so a check shipped
  inside the tarball only vouches for itself. Two things do reach older images:
  - control over who can publish a release and its assets;
  - `--hash` lines in `requirements.txt`, which the frozen `pip install -r` enforces.
- **Rerunning reaches past first boot.** `mptcp_off` runs on every Caddy start, not once. If a user
  replaced the Caddy binary on an image without the baked fix, the next restart would wrap the
  user's binary.
- **Images and the portal release move at different times.** Images bake `portal-aio/` from the
  checkout, alongside `ROOT/`. Base, the conversion script and the externals' scheduled builds
  all do this; derivatives inherit base's portal. Base is promoted from its branch before merge.
  The two trees change
  together often: 8 of the 22 commits to `portal-aio/` since 2026-03 also changed `ROOT/`. While a
  release is held in QA, an image bakes a portal version ahead of latest. Today's updater would then
  downgrade it at first boot, because it updates on any mismatch.
- **Downgrades undo fixes.** If an old release is ever marked latest, by mistake or otherwise,
  every new container that updates on mismatch downgrades to it. That silently
  removes the fixes shipped since.
- **Two fixes can ship between image builds.** A container takes exactly one release: whatever is
  latest
  at its first boot. So a fix shipped in one release must also be in every later release, or older
  images that first boot later never get it.

## Options considered

- **A. Container ownership after first boot, with a release gate (chosen).** The portal may change
  a container only during its first boot, and those changes are permanent. After that the
  container belongs to the user, and we never touch it again. Safety comes from the gate before
  latest, not from undo. *Trade-off:* a bad change that passes QA stays on every container that
  first-booted with it. The gate is the control.
- **B. A per-boot remote kill or disable check.** Rejected. After first boot the container is the
  user's, and a per-boot check would be us touching it again. It would also add a network call to
  every boot, put more pressure on the unauthenticated GitHub rate limit, and add a trust surface.
  Nothing in this ADR acts after first boot, so the same reasoning holds
  throughout. *Dissent recorded:* the critical review rated the lack of undo a blocker, and
  would downgrade it only with this check plus a pre-latest gate. This ADR takes the gate and
  refuses the check (see Consequences).
- **C. Ship an `undo()` in a later release.** Rejected: a container that already updated never
  receives a later release, so the undo never reaches the containers that need it.
- **D. Keep ADR 0015's merge-equals-ship trigger.** Rejected for any payload beyond portal code. It
  is a fleet-wide root change with no pause, and under A there is no undo.
- **E. A declarative general runner,** with an action vocabulary, applies-if fields and a per-arch
  binary fetcher. Rejected. One fix has shipped this way since the first-boot update arrived in
  2025-03, so a general
  vocabulary is speculative. A real applies-if condition (for ADR 0061, "the baked script has
  this exact line") is clearer as code. A fetcher adds downloads at first boot, a per-arch build,
  and delay ahead of `/etc/portal.yaml`. ADR 0061 showed an environment variable or config usually
  beats a new binary.
- **F. One-off modules per incident, with no shared harness.** Rejected. Each copies about 140
  lines of file-safety code by hand, and nothing enforces the first-boot rule, the time budget or
  the gate.
- **G. A baked, signed, emergency-patch manifest.** Not adopted for this purpose. It reaches only
  images built after it ships, which is the opposite of why this channel matters, and it adds a
  second channel beside the portal.
- **H. Sign the tarball as the gate.** Rejected as a prerequisite: older updaters cannot verify a
  signature. Signing for new images remains possible, as its own decision.
- **I. Put paths outside `portal-aio/` in the tarball.** The frozen updater would unpack them
  anywhere under `/opt`. Rejected: it breaks ADR 0015's archive binding and would overwrite
  per-image overlays. Files outside `portal-aio/` are changed only by harness code, with the file
  checks below.
- **J. Block image promotion while a release is held.** Rejected: one held release would freeze
  urgent rebuilds across the image family.
- **J′. Keep the "update on any mismatch" updater, with a rule that `ROOT/` works with two
  portals.** Rejected. Marking an old release as latest would then downgrade every new container and
  undo shipped fixes. It would also need a two-portal compatibility rule on every coupled change.
  The chosen never-downgrade updater (Decision 4) costs this: a demoted version cannot be pulled
  back from images that baked it. That is fixed forward, by a higher version.
- **J″. Bake the latest published release instead of the tree.** Rejected. It splits `ROOT/` from
  `portal-aio/`, so a coupled change would ship new `ROOT/` with the old portal untested. It also
  breaks promoting base from its branch, because a release can only be cut from `main`.
- **L. Keep all portal code compatible with the oldest supported image's Python (3.10).**
  Adopted for now. Each vintage runs the portal on its own venv's Python: 3.10 on the 2025-03
  vintage, 3.11 since 2025-06. Every release therefore stays installable and runnable on 3.10, as
  releases are today. Python 3.10 is past end of life and dependencies are dropping it, so this
  obligation has a cost. Moving the portal to one newer Python on every vintage is left to a
  separate proposal. It needs a
  first-boot venv rebuild on every older image, and a failed rebuild must not cost a container its
  apps, so it is decided on its own evidence.

## Decision

1. **The ownership rule.** We change a container only during its first boot. After that it
   belongs to the user, and we never act on it again: no later boot or service restart re-applies
   anything. If the user runs `update-portal` themselves, they get that release's fixes. It is
   their install, and we never run it for them. Applied changes are permanent. There is no undo
   and no remote kill.
   - **A user's own install warns first, and can be backed out of.**
     - **New images.** `update-portal`, run from a terminal outside first boot, first fetches the
       release and reads it in a staging directory. It lists each fix the release would apply to
       this container, and each
       program a supervisor reread would restart. It then asks to continue, defaulting to no.
       `--yes` skips the question for scripts. Run without a terminal and without `--yes`, as
       from a provisioning or onstart script, it refuses, changes nothing, and says to add
       `--yes`.
     - **A launch pin does not steer a user's own install.** `PORTAL_VERSION` applies at first
       boot only. Run by hand, `update-portal` installs the version given with `-v`, or latest
       without it. When `PORTAL_VERSION` is set and names a different version, the prompt names
       both ("launched pinned to vX, installing vY"); with `--yes` it prints the same line.
       Existing images already behave this way: no updater ever read the variable, and only the
       first-boot stage passed it as `-v`.
     - **The variable is not evidence of a pin on older images.** Images from 2025-03 to 2026-03
       append `PORTAL_VERSION=<installed version>` to `/etc/environment` after every install. On
       images from 2025-08 the line survives first boot, so the variable is set in login shells
       and supervisor programs on containers nobody pinned. 2025-03 images rewrite the file later
       in their first boot, so there it survives only when the template set it. The
       harness and `portal-fixes` never read it to decide anything.
     - **Older images.** Their frozen `update-portal` cannot ask, and has already unpacked the
       tarball before any of our code runs. So when the harness finds it is running outside first
       boot on such an image, it applies nothing. It records the pending fixes, writes a notice
       naming them to its log, and the instance portal shows the same notice on its home page.
       The user applies them with `/opt/portal-aio/bin/portal-fixes apply`, which lists them and
       asks first, or leaves them unapplied. The command lives inside `portal-aio/` (option I),
       and the notice names its full path.
     - **Outside first boot** is decided per image generation:
       - **New images.** The first-boot block writes `/.first_boot_finished` as soon as its
         update and harness run have finished, before stage 05 and long before provisioning.
         Outside first boot means that file exists. A run of `update-portal` from a terminal is
         therefore always a user install, even during provisioning.
       - **Older images.** Their boot writes no end marker, and `/.first_boot_complete` is
         written early in the first boot, after the first-boot update has unpacked, on every
         vintage. So outside first boot means `MANIFEST` changed after `/.first_boot_complete`
         was written (by more than a couple of seconds' timestamp slack): a later unpack was the
         user's own. `MANIFEST` is read by inode change time, which unpacking always sets, because
         tar restores the modification time from the archive. Only that file is read, because
         imports write `__pycache__` elsewhere in the tree.
       - **An interrupted first boot still counts as first boot.** On images from 2025-08, the
         update unpacks at stage 25 and stages 35 to 55 run before supervisord, so a stop or
         preemption can land between the two. When the harness has never run for the installed
         identity and `MANIFEST` is not newer than `/.first_boot_complete`, the release was
         installed by the first boot, so the run is the first-boot install, whatever PID 1's
         age. A `supervisorctl restart caddy` after a hand-run `update-portal`, in the
         container's first session, is therefore outside first boot.
       - **The one exception.** A start that follows a first-boot reboot the harness itself
         requested (Decision 3) still counts as first boot, once, until that boot has run in
         full.

2. **The release gate (supersedes ADR 0015's trigger).**
   - **Publish as a prerelease.** A merge that bumps `portal-aio/VERSION` publishes the release as
     a GitHub prerelease. `releases/latest` ignores prereleases, so nothing reaches the fleet yet.
     At publish, the workflow records the asset's sha256 and checks that the archive's contents
     match its `MANIFEST`.
   - **Test the exact asset.** QA boots the matrix (binding conditions), with `PORTAL_VERSION`
     pinned to that prerelease. Every vintage's frozen updater honours the pin. Each cell checks
     the installed `/opt/portal-aio` files against the release's `MANIFEST`. The frozen updater
     deletes the
     downloaded tarball, so the cell cannot hash the tarball itself.
   - **Promote only on a pass.** Promotion to latest happens only on a passing matrix, and only
     after re-checking the items below. The first promotion also waits for a reviewer's approval
     on the `portal-release` environment; whether later promotions stay reviewed or run
     automatically on a pass is the owner's decision after that first one. Each promotion is
     announced, as each hold is.
     - the asset's sha256;
     - that the version is strictly greater than the current latest;

     and it sets `make_latest` explicitly.
   - **Serialise and restrict.** Publish and promote share one concurrency group. Dispatch refuses
     any commit not reachable from `main`.
   - **Demote and re-run.**
     - A `demote vX` action flips a bad latest back to prerelease, records why, posts to Slack, and
       writes a deny record. Promote and `rerun-matrix` both refuse any version with a deny record,
       so a demoted release never reaches latest again; its fix ships as a higher version.
     - **The deny list lives on its own branch, where an updater can read it.** Release assets
       are immutable once published (binding conditions), and release notes are read only
       through the rate-limited API. The list is one file, one version and reason per line, on a
       dedicated branch that only the release workflow's demote action can push to, so the
       workflow never needs push access to `main`. Any other change to it alerts. Updaters read
       it from its raw URL, which needs no API call; the raw cache can lag a few
       minutes behind a demote. A matrix failure alone adds nothing, so `rerun-matrix` can still
       re-test a release held by a flaky cell.
     - Demote is the one fleet-wide lever. New first boots stop taking the release as soon as
       GitHub's `releases/latest` cache refreshes, in about a minute.
     - `rerun-matrix vX` re-tests a release that is still a prerelease and has no deny record.
   - **When QA capacity is missing.** If QA offers are unavailable, the release waits. A recorded
     human override, naming the cells skipped and the reason, is the only exception.
   - **Kept from ADR 0015:** the strict-semver, draft-republish and single-SHA guards.

3. **A thin first-boot harness in `portal-aio`** replaces one-off modules, starting with
   `mptcp_off`. It contains:
   - an explicit registry of fixes, each a small module with code for when it applies and code to
     apply it;
   - the shared file-safety primitives taken from `mptcp_off`;
   - a time budget;
   - a record of what each container applied;
   - **two entry points, one run.**
     - On existing images it runs from `caddy_config_manager.py`, as above.
     - On new images, `update-portal` runs it directly after installing (Decision 4).
     - The marker makes sure it runs once per install, whichever entry point reaches it first.
   - **supervisor reread where a fix needs it.** A fix that changes a supervisor program
     definition, or a binary a running program already started from, asks for it. The harness
     then runs `supervisorctl reread` followed by `update`, which restarts only the programs whose
     definitions changed, or a named `restart` for a changed binary. It does this once, as its last
     step, detached, after its own record is written.
     - It never runs a full `supervisorctl reload`, which would restart `caddy.sh` and the harness
       with it.
     - On new images the harness runs before supervisord starts, so no reread is needed.
   - **How a fix reaches its target on existing images.** Each fix uses the first of these routes
     that reaches its target, and declares it:
     1. on first boot, because the target starts after the hook;
     2. on first boot, through a named supervisor restart. For boot-shell environment on images
        since 2025-08, the fix writes the value to `/etc/environment` first;
     3. from the next boot;
     4. **only in an extreme case, a first-boot reboot.**
   - **The first-boot reboot.** It exists for a fix so urgent that waiting for the next boot is not
     acceptable, on an image whose hook runs too late. There may never be a valid reason to use
     it. A fix that uses it amends this ADR, recording the reason. Its rules:
     - The reboot goes through the platform. The harness calls the instance's own reboot with
       `CONTAINER_API_KEY`. It never kills PID 1.
     - It happens at most once per portal install, is recorded, and is never retried. One reboot
       covers every fix that needs one.
     - It is the harness's last step, detached, after the record is written, and it runs straight
       away at the hook, before the user's workload is under way.
     - Before rebooting, the harness writes a "reboot requested" marker. The next start then counts
       as first boot, once, until that boot has run in full (Decision 1).
     - Serverless workers are never rebooted. On them, such a fix takes effect from the next boot.
     - It may be used only while the live checks recorded in the binding conditions still hold.
   - **serverless.** On existing images, serverless workers stay outside the channel, apart from
     2025-03 workers, whose updater does not skip them. On new images they are inside it
     (Decision 4). A fix runs on serverless workers that reach the harness unless it skips them.
   - **A fix may skip serverless workers.** A fix lists the conditions it skips on in `SKIP_ON`,
     and the harness records the skip. The one condition is `serverless`, read from the
     platform's `SERVERLESS` variable (ADR 0038): a fix that only matters to services a worker
     stops, or that would cost a worker's cold start more than it saves, skips them. A container
     cannot reliably tell whether it is on demand or interruptible, so there is no condition for
     that.
   - **A fix may run a command, by name.** Some fixes need a command after a file change, such as
     `update-ca-certificates` after a CA bundle change or `ldconfig` after a library change. A
     fix declares each command in a `COMMANDS` table in its own source and runs it through the
     harness, under these rules:
     - **Fixed in source.** Each entry is an absolute path to a program the image or the release
       carries, then literal arguments. Nothing in it comes from the environment, a file or the
       release manifest.
     - **No shell.** The arguments go straight to the program: no `sh -c`, pipes, globbing or
       expansion. It runs with no stdin, a fixed minimal environment, and a timeout.
     - **Safe to repeat.** The fix checks whether the command's effect is already present and
       records "already done" instead of running it again. If it cannot tell, it does not run.
     - **Recorded and checked.** The exit code and a hash of the output go into the container's
       record. A non-zero exit is a failed fix, which the matrix catches.
     - **Timed like any other action.** A quick command can run in the early slot within its
       budget; a slow one runs detached after `/etc/portal.yaml`, still within first boot.
     - **Reviewed.** A lint rule refuses any other way of running a program from a fix:
       `subprocess`, `os.system` and the like, `shell=True`, or a command not in the fix's table.
       Each command a fix adds is listed in the release notes of the release that adds it.

   - **A fix may update packages, for security.** A security fix is usually a newer package from
     the distribution or a package index, and shipping it with its dependencies would make every
     release heavy. So a fix may raise packages through each manager the images ship: apt, the
     Python environments' pip, conda, and npm for every Node that nvm installed. It declares a
     `PACKAGE_UPDATES` table of (manager, environment, package, minimum version), and every
     manager follows the same rules:
     - **Upgrade-only.** A package that is not installed is left alone. One already at or above
       its minimum is recorded as already patched, so the action is safe to repeat. Packages are
       never removed, recommended extras are not installed, and the user's own config files are
       kept.
     - **Everything else stays put.** apt upgrades only the named packages; pip installs against
       the environment's current versions as constraints, binary wheels only, so no build code
       runs; conda freezes what is installed.
     - **Within the minimum's major version.** pip and conda ask for at least the minimum and below
       its next major version, and npm for `^minimum`, so a security update never becomes a feature
       jump (npm 11 to 12 was the case found in testing).
     - **Two phases.** The download (`apt-get update` and the packages; the wheels; the conda
       packages; npm's cache) is the phase a stall guard may stop, and killing it is safe because nothing
       is installed yet. If a template sets a time cap, every download in a run shares it; queries
       have their own short timeout. The install then runs only from what was downloaded, with no network
       wait, and is never cut off: a package manager killed mid-install leaves its state
       half-configured for the user's next run.
     - **No collisions.** A user's provisioning runs apt and pip without waiting for their locks,
       so ours must never run beside it. On new images the action runs at the start of the boot,
       before provisioning begins. On existing images the detached run waits until provisioning
       has finished: every vintage sets `/.provisioning` before supervisord starts and removes it
       when provisioning ends. After 2 hours it records a failure instead of running. apt also
       waits a bounded time for the dpkg lock, for a user's own apt run. The platform's launcher
       finishes its own apt steps before our boot starts (ADR 0064's measurements), so it is never
       contended.
     - **Verified and recorded.** Each package's version is read again afterwards: at or above the
       minimum is applied, anything else is a failed fix. A version the manager will not report
       (a venv without pip, say) is a failed fix too, never "not installed": only a package the
       manager says is absent is left alone. The record keeps both versions, and the matrix
       checks the outcome.
     - **When it runs.** On new images, in the start-of-boot block, so the patch lands before any
       service starts. On existing images, the stage-65 hook hands it to a detached run after
       `/etc/portal.yaml`, followed by a named restart of the services the fix lists. Serverless
       workers take it unless the fix skips them.
     - **The manager updates itself first.** A manager's own packages (apt and dpkg, pip, conda,
       npm) are raised as ordinary entries, and go first, in their own two phases, so the rest of
       that manager's packages are installed by the patched manager. If the manager's own update
       fails, the rest of its packages are not attempted and are recorded as failed.
     - **A tool before what it installs.** Managers run in a fixed order: apt, uv, conda, pip,
       Node, npm, then venv Python patch releases. So uv is current before it fetches a Python,
       and npm's rows see a Node release installed moments earlier. Each manager's environments
       are worked out when its turn comes.
     - **Runtimes no manager installs.** Three tools are not packages of any manager the images
       ship, and each has its own entry. Each installs the new release beside the old one, which
       stays, and switches over only once the new one is verified.
       - **uv** (`uv`, an exact release). Images install uv as a standalone binary in
         `/usr/local/bin`, where `uv self update` is switched off. The entry downloads exactly that
         release for the machine's architecture from uv's GitHub releases, checks it against the
         release's published sha256, and swaps `uv` and `uvx` in by rename, keeping the old
         binaries. Keeping uv current is worth doing on its own; it is also what lets an image
         fetch Python patch releases published after the image was built.
       - **Node** (`node`, a major line, a minimum release in it). `nvm install` fetches the
         line's newest release from nodejs.org and checks it against the published checksums.
         The release must reach the minimum and Node's trust floor (ADR 0058). Its global npm
         packages are reinstalled from the old release, and only then does it become nvm's
         default, which is how login shells and services find Node. A failed install removes its
         own partial release and leaves the default alone.
       - **A venv's Python patch release** (`python-runtime`, the venv, its minor version, a
         minimum patch release). Only for venvs on a uv-managed interpreter: an apt or conda
         Python is raised by an apt or conda entry. `uv python install` adds the patch release
         beside the old one. A venv on uv's minor-version link (uv 0.8 and later, which current
         images use) follows the link; a venv pinned to an exact patch directory (older uv) is
         repointed by rewriting its `pyvenv.cfg` and `bin/python` link, each swapped by rename.
         Installed packages carry over, because a patch release keeps the binary interface. It
         never moves a venv to another minor version. A venv whose Python is not uv-managed is
         left alone rather than failed, so one fix covers every image. The patch release must be
         one uv knows: a fix that needs a newer one than the image's uv also carries a `uv` row,
         which runs first.
     - **Only this way.** A lint rule (L123) refuses a package manager run as a named command, and
       a `PACKAGE_UPDATES` table that is not literal or whose runtime rows leave their line.

     The accepted cost: what is installed is whatever the archive or index serves that day, at or
     above the minimum, so QA tests that day's result rather than fixed bits. The gate checks that
     what ships works as intended; it does not prove which versions a later boot receives. That is
     the trust image builds already place in the same archives and indexes, which they also
     install from by version range without hashes. Pinning rows to exact versions or hashes was
     considered and rejected for now, for that reason; it would come with the same change to
     image builds. For the same reason npm runs packages' install scripts, as image builds do
     (`--ignore-scripts` was considered and rejected). The cost, accepted: a bad release of a
     package a row names keeps resolving while the fix stays in the registry, and demoting the
     portal release does not stop it; only a release that changes the row does. A fix's first boot is slower by the update's download, only on containers that
     need it, and the boot waits for it (see "No time cap").

   It has no general binary fetcher. Apart from package updates, no fix downloads anything; a fix
   that genuinely needs another download amends this ADR.

4. **New images: fixes in source, and a portal update that never downgrades.**
   - **Fixes in source.** New images carry their fixes in source, as ADR 0061's baked export and
     lint rule L118 do. There is no new baked boot stage. *Dissent recorded:* one reviewer
     preferred one. An image ships only with every fix its portal registers already in its own
     source: a lint rule (L124) requires every registered fix to be listed as baked, and L121
     requires each listed fix's source change to be present. So a baked portal never runs its own
     fixes. Taking a fix's source change back out means taking the fix off the registry in the
     same change.
   - **The hook moves to the start of the boot on new images.**
     - **What moves.** The vast-cli update, the portal update and the harness run first, before
       stage 05. Image-specific first-boot scripts, such as the `99-package-install.sh` that
       several engine images ship, stay at stage 25, after the environment is set up.
       Serverless workers still skip the vast-cli update.
     - **A generous ceiling that defers, never drops.** Delivering a release's fixes matters more
       than a fast first boot, so the boot waits for the lookup, the downloads (the release, its
       missing dependencies, a package fix's packages) and the installs. It says so in the
       container log: before the lookup, when it installs, when a fix updates packages, and every
       minute while a download runs. It waits up to a whole-run ceiling, 60 minutes by default
       (`PORTAL_UPDATE_DEADLINE`, in seconds, read from the process environment or
       `/etc/environment`). Within it, a download gives up on a connection that makes no
       progress: the managers' own network timeouts, curl's minimum speed, nvm's download watched
       for growth (stopped after 2 minutes without any), and a 30-minute backstop on every
       attempt, the release's own download and its dependencies' included. A stalled or failed
       download is retried, up to 3 attempts with a pause between, after removing any partial
       result; it is safe to repeat because nothing is installed until the download phase is
       complete. Installs are never cut off: a package manager killed mid-install leaves its state
       half-configured. Once the release's dependencies are installed its files always follow,
       because the baked portal on the new dependencies is a pairing nobody tested; its `VERSION`
       is written last, so an install cut off part-way still reads as the old version.

       When the ceiling is reached, the update is deferred, not dropped. Before the install, the
       boot carries on with the baked portal and the next start runs the update once more. During
       a fix's downloads, the fix is recorded as deferred and the next start finishes it once, by
       the same path as an interrupted first boot. A second deferral is final, as a second
       interruption is. *Rejected:* a default cap of 120 seconds, which the live test showed a Node
       download alone can exceed on a slow host, leaving the fix failed for good; no ceiling at
       all, whose worst case on a slow but live connection is hours; a lower per-attempt backstop
       alone, still unbounded in total; and running the rest after services start on new images,
       which starts services before the patch lands.
     - **One entry point on new images.** `update-portal` runs the harness directly after
       installing. On first boot, fixes then reach every boot stage and every supervised program
       before it runs, so new images never need a named restart or a reboot to deliver a fix.
       The stage-65 entry point in `caddy_config_manager.main()` never runs the harness on a new
       image; it exists for older images. The harness applies fixes only for a release
       `update-portal` installed at that first boot, recorded before any file moves. So an
       opt-out, an already-current portal, a skipped pin, a failed lookup or an expired cap (when a
       template sets one) means
       no fixes run, and the image keeps the fixes it baked. The one exception is finishing a
       first-boot run of an installed release that a stop cut off.
     - **How a fix changes a later stage.** Stages are sourced into the boot shell, and the harness
       is a separate process. So a fix changes a later stage by editing that stage's file, or a
       file the stage reads, before the shell reaches it. Each stage file is read only when it
       runs. A fix cannot add a new stage file, because the list of stages is fixed when the boot
       starts.
     - **What the early block takes over from the stages it now precedes:**
       - it reads `SERVERLESS`, `PORTAL_*` and its flags from both the process environment and
         `/etc/environment`, which used to be stage 10's job;
       - it creates its own log directory, which used to be stage 11's job;
       - it applies stage 12's thread caps to its own child processes, so `pip` and `uv` cannot
         exhaust a high-core host's pid limit.

       Fixes that run there see the environment before stage 10 normalises it.
     - **Better for template hotfixes.** `HOTFIX_SCRIPT` (stage 15) now runs after the release, so
       a template's hotfix has the last word instead of being overwritten. The cost: on a new
       image's first boot it can no longer repair the updater or the harness, which have already
       run. A broken new-image updater is fixed by rebuilding images.
     - This is not a new boot stage. The base image's own first-boot steps move.
     - *Rejected:* placing the block straight after stage 12. That keeps the normalised environment
       and thread caps without extra work, but gives up first-boot reach of stages 05 to 12.
       Stage 12 has already needed a fleet-wide fix once.
   - **Serverless workers take the update on new images.** Stage 25 no longer skips the portal
     update for `SERVERLESS=true`. This brings every worker under the same fixes as on-demand
     instances, and puts GitHub on a worker's first boot. An endpoint that wants today's cold
     start opts out with `--no-update-portal` or `PORTAL_UPDATE=false`; the opt-out is the
     template owner's choice. Three things keep the cost down:
     - **Worker mode.** A worker never serves the portal (`caddy.sh` exits on it), so on a worker
       the update is the release's fixes only: the updater stages and verifies the release, does
       not install its dependencies or files, and runs the staged release's harness against the
       container. The installed portal stays the baked one.
     - **Nothing to fetch when the image already has the release.** Each release publishes its
       portal identity as a small asset beside the archive. When it matches the installed
       portal's, the updater downloads nothing more and runs no fixes: the image carries them
       (L124). This is the steady state for every image built from a released tree.
     - **Running on workers is a reviewed choice.** Every package fix declares `SKIP_ON`
       explicitly, `()` or `("serverless",)` (L125), so its cost on workers is decided when it is
       written, not left to a default.
   - **An environment variable also opts out on new images.** `PORTAL_UPDATE=false` (any case)
     does exactly what `--no-update-portal` does: no lookup, no download, no harness run.
     - **Why.** The flag lives in a different place in each launch mode: on the `entrypoint.sh`
       line in the onstart script in SSH and Jupyter mode, and in the Docker arguments in
       entrypoint mode, where the onstart field replaces the container's start command. A
       template, or a user who can set only environment variables, had no way to opt out.
     - **Where it is read.** The early first-boot block reads it from the launch environment and
       from `/etc/environment`, as it reads `SERVERLESS` and the other `PORTAL_*` values.
     - **Precedence.** Either the flag or the variable turns the update off, and neither turns it
       back on. It also overrides a `PORTAL_VERSION` pin: with both set, nothing is downloaded.
       It applies to serverless workers too.
     - **Scope.** It governs the automatic first-boot update only. A user who runs
       `update-portal` themselves is making their own install (Decision 1) and gets the usual
       prompt, which notes that the container opted out at launch.
     - **Older images ignore it.** They keep the flag (from 2025-08) and the pin.
   - **Baking stays as it is.** Images keep baking `portal-aio/` from their own tree, and base is
     still promoted from its branch before merge. An image's portal is the one its own promote QA
     tested with its own `ROOT/`.
   - **An unreleased bake gets a development version.** An image built from a tree that is not
     exactly a published release bakes `<VERSION>-dev.g<short sha>`. That sorts below the release
     of the same number under semver precedence, so the image upgrades to the real release once it
     is published. The release workflow's strict-semver guard rejects `-dev` versions. VERSION then
     names one payload only, and a QA cell pinned to a prerelease always downloads it rather than
     keeping a baked copy of the same name.
     - **Why the `g`.** Semver compares an all-digit identifier as a number and forbids a leading
       zero in one. A short sha of only decimal digits would compare as a number, and one
       starting with `0` would not parse. The `g` prefix, as in `git describe`, keeps the
       identifier alphanumeric for every sha.
     - **A portal change must raise VERSION.** CI fails any change under `portal-aio/` unless
       `portal-aio/VERSION` is higher than every published version, prereleases included.
       Without this, a change merged under the current number bakes `<current>-dev.g<sha>`, which
       sorts below the published `<current>`. The image's first boot would then replace its own
       tested portal with the older release.
     - **A version number names one tree.** A VERSION may not reuse a number already taken by a
       published release or by a promoted bake with a different portal identity (read from the
       registry labels, with the `-dev` suffix removed). CI, image promotion and the release
       workflow's publish
       step all apply this test. When two branches bump to the same number, whichever is
       promoted or published first takes it, and the other fails all three until it bumps again.
       Without this, a release from one branch would sort above the other branch's promoted
       `-dev` bake and replace its portal at first boot, under a `ROOT/` it was never tested
       with.
     - **Promotion refuses a denied bake.** Image promotion fails when the image's baked portal
       version, with any `-dev` suffix removed, is on the deny list.
     - **The registry is the record of baked portals.** Every image build sets two labels: the
       baked portal VERSION and the portal identity (the `MANIFEST` hash). Derivatives inherit
       both from their base. The release workflow reads them for every promoted image from
       Docker Hub's image configs, without pulling layers. There is no separate record to
       drift.
     - **No release reaches an image baked above latest.** Never-downgrade keeps its baked
       portal until a higher version is latest. This happens in the window between promoting an
       image from its branch and its portal release reaching latest. Every release run lists
       those images. When a fix must reach them sooner, they are rebuilt with it, or their tag
       is repointed, as for a bad bake.
     - **No release overtakes a promoted bake that never landed.** At promote to latest, for
       every promoted bake the release would sort above, the workflow checks that the bake's
       portal identity was on `main` at some commit up to the release's commit, by reading
       `portal-aio/MANIFEST` along main's first-parent history. Content is compared, not
       commits, because this repo squash merges. If the identity never landed, promotion
       refuses and names the images: the release would replace their tested portal, under a
       `ROOT/` it was never tested with. The same check warns when the prerelease is published.
       It clears when the other branch lands first, or with a recorded override naming the
       images, for example for an abandoned branch whose tags are rebuilt or repointed.
     - **A branch promoted twice bumps VERSION each time.** An earlier promotion's images keep
       their own portal, and under "a version number names one tree" the later promotion of a
       changed portal needs a new number. Numbers are cheap; silently giving the earlier images
       a portal they were not tested with is not. An image promoted after a release is already
       latest bakes a higher version, so never-downgrade keeps it, and its own release must
       contain latest under the VERSION rule.
   - **Overrides are checked before unpacking.** With `PORTAL_DOWNLOAD_URL`, the new-image
     updater downloads to a staging directory and reads the archive's `VERSION` and `MANIFEST`
     before anything is unpacked into `/opt`, then applies the never-downgrade and deny checks
     below. With `PORTAL_REPO`, the deny list is read from that repository.
   - **A frozen updater honours five template settings.** `PORTAL_VERSION`, `PORTAL_UPDATE`,
     `PORTAL_UPDATE_DEADLINE`, `PORTAL_REPO` and `PORTAL_DOWNLOAD_URL`. The updater is frozen
     into every image that carries it, so each setting it honours is a contract for as long as
     that image is launched. Its other knobs (the install root, the deny-list address, the
     download heartbeat and backstop) are honoured only by its sandbox tests, behind
     `PORTAL_UPDATE_TEST=1`.
   - **Never downgrade.** The new-image updater only ever moves to a higher version, comparing
     versions by semver precedence. No request moves it lower, and there is no override. An older
     portal on a newer image is a combination nobody tests, and supporting it is not worth the
     upkeep.
     - **A first-boot pin below the baked version is skipped.** At first boot the updater honours
       an explicit `PORTAL_VERSION` pin that is higher than the installed version. A pin that is
       lower is not installed: the boot logs both versions and the skip, keeps the baked portal,
       and carries on. The boot does not fail. A pin equal to the installed version downloads
       nothing.
     - **A user's own install never downgrades.** Run by hand, `update-portal -v` with a version
       lower than the installed one refuses: it names both versions, exits non-zero, and changes
       nothing. The installed version itself is a no-op.
     - **A denied version is never installed.** Before installing a pinned or hand-requested
       version, the updater reads the deny list. If the version is on it, a first boot logs the
       reason and keeps the installed portal, and a hand run prints the reason and exits
       non-zero. If the list cannot be read, the pinned or requested version is not installed,
       so a failed check never installs. Latest is never denied, because demote removes it from
       latest first.
     - **An unpromoted version says so.** A hand run whose requested version is higher than
       latest is installing a prerelease that has not passed, or not finished, its matrix. The
       prompt says so before asking; with `--yes` it prints the same line. A first-boot pin to a
       prerelease installs without a prompt, as QA cells need.
     - An image whose baked portal is ahead of latest keeps it, so a held release never blocks image
       promotion.
     - Marking an old release as latest cannot undo fixes on new images.
     - Older images never bake ahead of latest, so their frozen updater only ever upgrades in
       practice, apart from an explicit pin. Their pins below the baked version still downgrade;
       that updater cannot be changed.
   - **Fixing a bad release means fixing forward.** Demote stops new first boots upgrading to it. A
     higher version replaces it everywhere a new first boot happens. Images that baked the bad
     version are rolled back by repointing their tag.
   - **Lookup.** The new-image updater finds latest through the `releases/latest/download/`
     redirect,
     with bounded retries, rather than the rate-limited API.

5. **Releases are cumulative, and nothing is applied twice.**
   - **Every release carries every active fix.** QA tests that combined set on every vintage. A fix
     shipped in v1.11.2 is still in v1.11.3, because an older image that first boots after v1.11.3
     never sees v1.11.2.
   - **The image's baked-fix list.** When a fix is folded into source, the image build records its
     id in a root-owned list of fixes that image already contains. A lint rule pairs each id with
     the source change that carries it, as L118 does for ADR 0061. The harness skips any fix in that
     list. Images built before the list exists fall back to each fix's own detection.
   - **The container's applied record.** Each fix reads which fixes this container has already
     applied (the harness's records) and treats its own earlier work as done, for example when the
     user later runs `update-portal` themselves. The harness does not skip such a fix outright: a
     superseding fix must still run where an earlier one applied.
   - **Superseding, and two fixes on one file.** Two releases can need to change the same file.
     For example, a later fix may need Caddy to run with a second setting, on top of the wrapper
     `mptcp_off` already installs. These rules apply. The worked example at the end of this ADR
     takes one binary through two fixes.
     - **Each file has one owner.** Each fix declares, by path, the files it changes: its targets.
       A binary, the wrapper that replaces it and a config drop-in are each a target. Two fixes in
       one registry may share a target only when one supersedes the other. Fixes on different
       files are independent, even when they affect the same service, and run in registry order.
     - **A later fix on the same file supersedes the earlier one.** Fixes never stack: the harness
       never wraps a wrapper, and never edits another fix's edit. The later fix lists every fix it
       replaces in `supersedes:`, the whole chain, so a third fix on the same file names both
       earlier ones. The harness never runs a superseded fix, and a superseded fix leaves the
       registry in the release that adds its replacement (Decision 6).
     - **Superseding upgrades; it never skips.** The superseding fix takes the file from any state
       an earlier fix in its chain could have left, to its own state. An earlier fix in the
       container's applied record never stops it. That record means the user has run
       `update-portal` themselves on a container that took the earlier fix at first boot, and
       their install should get the new fix.
     - **Every starting state is named and recognised.** A fix lists the states it handles, and
       recognises each by a marker or a known sha256. It never infers a state from anything
       else. The states are:
       - as shipped, in an image with none of the chain's fixes;
       - left by each superseded fix, applied by the harness;
       - each superseded fix baked into the image's source;
       - this fix baked or already applied, where there is nothing to do;
       - anything else. The file is not ours (a user changed it), so the fix leaves it alone and
         records why.

       There is no partial state to handle, because every action is atomic.
     - **The container's history narrows the states.** Each fix records the release that
       introduced it, and the harness records each fix's outcome on each file it changed, in a
       root-owned log that every later run appends to. That log is the history fixes read; the
       marker keeps a list of past runs for people. It survives because the updater overwrites
       `VERSION` before the harness runs. On a later install, a fix accepts only the states that
       history allows, file by file. For example, once an earlier fix in the chain has changed a
       file here, its own state is expected on that file. Finding that file "as shipped" instead
       means someone replaced it after that fix, and the fix treats it as not ours. A file the
       earlier fix never reached here (absent then, installed since) is still the fix's to change.
     - **The original stays where the first fix put it.** A superseding fix changes only what an
       earlier fix wrote, such as the wrapper or the drop-in. It never moves the original again,
       so the shipped binary is kept exactly once.
     - **Files the harness writes say who wrote them.** Each wrapper or drop-in carries a marker
       naming the fix that wrote it. A superseding fix also keeps the markers of the fixes it
       replaces. Suppose a user later installs an older release themselves. Its fix then finds its
       own marker and reports "already applied", rather than wrapping the newer wrapper. A fix
       that finds a harness marker from a fix it does not know leaves the file alone.

6. **Every supported vintage works with every release, and fixes roll off with the window.**
   - **The window.** Supported vintages are those built within the last 36 months, plus any older
     vintage given a recorded extension because it is still heavily launched. Extensions are
     recorded in the release workflow, with the reason. On 2026-11-04 the window covers every
     vintage that has an updater, back to 2025-03.
   - **Every release works on every supported vintage.** A release reaches latest only if it
     installs, starts the portal and passes its tier of the matrix. The full tier covers each
     supported vintage. Releases stay
     runnable on Python 3.10 (option L and the binding conditions), which is what makes this hold
     on the oldest vintage.
   - **The support floor is the oldest supported vintage.** It is the oldest vintage in the QA
     matrix.
   - **When a fix leaves the registry.** It leaves once the support floor is a vintage that bakes
     it, or in the release that adds a fix superseding it (Decision 5), whichever comes first.
   - **What retirement means.** It affects only future first boots on images older than the floor.
     Containers that already applied the fix keep it.

## Binding conditions

These hold for every fix delivered through this channel. If one is refused, the decision is void.

- **The harness runs once per portal install, finishing an interrupted first boot once.**
  - **Every portal carries its own identity.** `portal-aio/MANIFEST` is a committed file that
    lists every shipped file under `portal-aio/` with its sha256, apart from `VERSION`, which
    bakes and updaters rewrite. CI fails any change that leaves it out of date. Its own sha256 is
    the portal's identity. It is the same in the release
    archive and in a baked image, because both carry the committed file, and it survives a
    squash merge, because it depends only on content. The harness reads it offline. It records
    that identity in a root-owned marker, and runs only when the marker is absent or names a
    different identity. It is keyed on content, not VERSION, because one VERSION name has
    already denoted two payloads (`docs/invariants.md`). A portal
    changes in only two ways, our first-boot update or the user's own `update-portal`, so this
    means exactly first boot or a user install.
  - The marker also lists every release the harness has run under in this container, oldest first
    (Decision 5's history).
  - The marker records that a run has started before its first fix runs, and each fix's result is
    written as soon as that fix finishes, so a run cut off part-way leaves an exact record of what
    finished.
  - **An interrupted first boot is finished once.** A stop or a preemption during first boot, or
    during the detached run that follows it, would otherwise lose a security update for good. So
    on the next start, the fixes of that first-boot install that have no final record run once
    more: slow ones detached again, as before. This is safe because every action checks its own
    state first and reports "already applied" or "already patched". If dpkg was interrupted
    mid-install by our own run, its configuration is finished (`dpkg --configure -a`) before
    anything else, or every later `apt-get` refuses. A lock stops a resumed run overlapping one
    that is still alive. If the first boot is cut off a second time, the unfinished fixes are
    recorded as "interrupted twice", once, and not retried. A user's own install is not resumed; its
    fixes stay pending for `portal-fixes apply`.
  - A fix that ran to completion and failed is not retried on a later boot.
  - The behaviour is verified on the support floor vintage before the harness ships.
  - `mptcp_off` comes under this rule.

- **No release reaches latest without passing the matrix.** Every release is tested; how much of
  the matrix it needs depends on what it changes.
  - **The full matrix** runs when a release changes anything under `caddy_manager/` (the
    Caddyfile, authentication and TLS), the harness, the fix registry or any fix, the hook call
    site, or `requirements.txt`.
  - **The reduced matrix** runs when a release changes only the instance portal's own code, its
    UI and static assets, the tunnel manager, or documentation and tests. Any other change runs
    the full matrix. The reduced matrix is the
    support floor and the current production image, each in entrypoint mode and in the platform's
    Jupyter launch mode in direct form, on amd64, plus one current-vintage arm64 cell.
  - The workflow picks the tier from the diff against the current latest, and records the tier
    and the reason on the run. Every cell in the chosen tier blocks. The serverless timing runs
    on both tiers.
  - **The full matrix:**
    - **Vintages:**
      - the support floor (Decision 6). On 2026-11-04 that is the 2025-03 vintage, with its Python
        3.10 portal venv and no `uv`;
      - one image from 2025-06 or later with a Python 3.11 portal venv;
      - the current production image: the newest promoted image whose baked portal version sorts
        below the release under test;
      - one converted external image;
      - the most-launched derivative image.
    - **Launch modes:** entrypoint mode, the platform's SSH launch mode, and the platform's Jupyter
      launch mode in direct form. Between them they cover each way the update differs by launch
      mode: whether the platform's launcher runs first, where the opt-out flag goes, the boot's
      port-8080 mapping, and whether the platform or our boot supplies the TLS certificate.
    - **Architectures:** every cell runs on amd64, and arm64 cells run for each vintage that has
      arm64 images.
    - **Checks:**
      - the portal is reachable;
      - unauthenticated requests are refused on every public port;
      - every binary a fix wraps or replaces actually runs (`--version` through the delivered path);
      - each fix's own runtime check passes;
      - the installed files match the release's `MANIFEST`, and the harness record names its
        identity;
      - a second container start changes nothing.

  Every cell's image bakes a portal version below the release under test, so the release is
  downloaded and installed rather than skipped. The matrix's vintages and cells are named in the
  workflow.

- **After promotion, a scheduled canary watches the fleet path.** It boots unpinned images of the
  oldest and the current vintage, so the real `releases/latest` lookup is used. It checks, in each
  launch
  mode, that the harness records name the current latest and that each fix's runtime check
  passes. It alerts on failure.

- **The early slot is local and bounded.** Code at the top of `main()` does only local file
  operations from the tarball: wrap with environment, replace by known hash, config drop-in, and
  a named command quick enough for its budget. It is
  standard library only, runs under a total budget of 5 seconds, and has its import failure
  caught, so the config manager always runs and always writes `/etc/portal.yaml`. Anything slower
  runs
  detached, after `/etc/portal.yaml` is published and still within first boot. It runs in its own
  session, with stdio redirected and inherited file descriptors
  closed. It takes effect when the affected service next starts.

- **Every action keeps the original and is safe to keep forever,** as `.mptcp_off/<name>` does. The
  file rules of ADR 0061 carry over:
  - no symlinks, FIFOs or other non-regular files;
  - directory file descriptors;
  - a lock;
  - owner and mode preserved;
  - the process name preserved.

  A binary that runs as root is not wrapped or replaced if a non-root user can write to its
  directory. That includes group-writable directories whose group is one a non-root user belongs
  to: the image's `user` account is in group 0. This is not a privilege boundary, because `user`
  has sudo. It is an ownership check: a binary someone else maintains there is not ours to wrap.
  - **The portal's own tree is ours.** From the first gated release, the release workflow builds
    the archive with `tar.umask` 0022, so its directories unpack as 0755 and do not trip the
    check. Releases before it were archived with git's default 0002, and unpacking sets the
    archive's modes on existing directories, so a
    container that took one has a group-writable `/opt/portal-aio`. Directories under
    `/opt/portal-aio` that are owned by root, and whose mode matches a portal archive, therefore
    count as ours.
  - **Tested on an updated container.** A matrix cell on a 2025-06 or later vintage takes the
    release through the frozen updater and checks that Caddy is wrapped and listens as plain
    TCP. "Not ours" is never a passing outcome for a fix on the portal's own binaries.

  There is no shell action, no command built from anything but the fix's own source, and no
  download action other than package updates. A fix runs a program only as a named command, and
  updates packages only through the package update action (Decision 3), both enforced by lint rule
  L123. The package action itself calls nvm, which is a shell function, through two fixed scripts
  in the harness's source; the Node version and paths reach them as arguments, never as script
  text.

- **The registry is an explicit list that every tarball overwrites.** It is never a directory glob,
  because unpacking never deletes, and stale modules would otherwise run.

- **Every release runs on every supported vintage's Python.** Portal code and requirements stay
  installable and runnable on Python 3.10 and 3.11, the Pythons of the supported vintages' portal
  venvs, until a separate decision or the window says otherwise. CI tests on both on every change,
  and a
  lint rule refuses syntax or standard-library use newer than 3.10.

- **Each container records what it did.** Every fix writes a root-owned record: id, portal VERSION,
  asset hash, whether it applied, the action, before and after hashes, outcome and time. QA checks
  it.

- **Rollout order.** Images carrying the never-downgrade updater are promoted before the
  release workflow starts holding releases. Until then an image baked ahead of a held release
  would update on mismatch and downgrade to latest.

- **Publishing belongs to the release workflow.** Only the release workflow can create, edit or
  delete a portal release, its assets, or a `v*` tag. The same control covers the release
  workflow's own definition and the deny-list branch. Releases are immutable once published, and
  any change not made by the release workflow alerts. The gate depends on this control. The
  specifics of how it is enforced stay internal (ADR 0012).

- **The release workflow's new QA credentials are documented internally.** ADR 0015's "no
  repository secrets"
  no longer holds. The workflow fails closed without them.

- **Every fix states its retire condition when it lands:** the first image build that bakes it.
  The release workflow refuses a release carrying a fix whose retire condition the support floor
  has passed. Retirement is never silent.

- **Every fix is tested from every starting state it names** (Decision 5).
  - Unit tests build each state from fixture files, run the fix, and check the resulting file and
    the record.
  - A mutation test per fix proves the detection bites. For example, a corrupted marker or an
    unknown hash must come out as "not ours" and leave the file untouched.
  - The release workflow refuses a registry in which two fixes share a target with no
    `supersedes` relation between them, or which still lists a superseded fix.
  - The QA matrix needs no extra cells for this. The "as shipped" and "baked" states come from the
    matrix's own images. The other states arise only when a user installs a release themselves.

- **Every fix states when it takes effect.** For each fix, that is one of:
  - on first boot, because its target starts after the hook;
  - on first boot through a supervisor reread or restart, naming the programs;
  - from the next start of the program, or the next boot;
  - by a first-boot reboot, which only an amendment to this ADR can permit.

  QA checks the stated point on both entry points: the stage-65 hook on existing images and the
  start-of-boot hook on new ones.

- **The first-boot reboot rests on live checks.** It may be used only while every check below
  holds. Each check records the date it was run and its result. A failed check closes the route,
  and the fixes it would have served take effect from the next boot instead.
  - `CONTAINER_API_KEY` can reboot its own instance. *Held, 2026-11-04 and 2026-11-05,* on
    demand, interruptible and on a serverless worker, in Jupyter launch mode.
  - A rebooted instance keeps its GPU and comes straight back up, on demand and interruptible.
    *Held, 2026-11-05.*
  - `CONTAINER_API_KEY` is present on the oldest supported vintage. *Held, 2026-11-05.*
  - A provisioning run interrupted by the reboot recovers on the next start. *Held, 2026-11-05,*
    on the 2025-03 vintage.
  - What a serverless worker does when it is rebooted, and when its supervised pyworker is
    restarted. *Checked, 2026-11-04,* on one worker: a pyworker restart was not disruptive, and
    a reboot briefly failed requests. A fix that restarts any other program on a worker still
    needs its own check. Serverless workers stay excluded from the first-boot reboot
    (Decision 3).
  - Not yet checked: the reboot in entrypoint mode and in the platform's SSH launch mode.

- **Supervisor actions are limited.** Only `reread`, `update` and a named `restart` are allowed,
  once per run, detached, after the record is written. A test proves that no fix can trigger a
  full `reload`. A matrix check confirms that `caddy.sh` and the harness are not restarted by the
  harness's own action.

- **Serverless first boot is measured on new images.** On every release, a matrix cell boots a
  new image as a serverless worker. It checks that the release's fixes were recorded, that the
  portal is still the baked one, and that a second start changes nothing, and it reports how long
  the first-boot update block took, from the container's start to its end. The owner
  decided that no time limit holds a release (2026-11-06): the numbers are reported on every
  release so a rise is seen, and the whole-run ceiling bounds a worker's first boot as any
  other's. *Rejected:* holding a release that adds more than a set amount over the previous one,
  or more than an absolute limit; the measurement varies more between hosts than a release
  changes it, and worker mode and the identity skip already remove most of the cost.
- **The network failures and the ceiling are tested once per updater, not per release.** GitHub
  unreachable, GitHub up with the package index down, a short ceiling that defers, and a
  throttled connection with a stalled download each run once as a scripted live test before an
  image carrying the updater is promoted, and the result is recorded in the build status below.
  Each must fall back to the baked portal, or finish, and the boot must carry on. These cases test
  the updater, which is frozen into the image; a portal release does not change how it behaves.
  *Rejected:* running them as per-release cells, which multiplies the matrix for behaviour a
  release cannot change.

- **The user-install prompt is tested.** On a new image, `update-portal` from a terminal outside
  first boot lists the fixes and programs, and changes nothing when answered no. With `--yes`, or
  at first boot, it does not ask. Without a terminal and without `--yes`, it refuses and
  changes nothing. The test runs in the container's first session, without a restart, as well
  as after one, and once with the first boot interrupted between stage 25 and supervisord. On an
  older image, a user install leaves pending fixes
  unapplied until `portal-fixes apply` is run and confirmed. With `PORTAL_VERSION` set to a
  different
  version, a hand run without `-v` installs latest and the prompt names both versions. A hand
  run with `-v` lower than the installed version refuses, exits non-zero and leaves every file
  unchanged. A first boot with `PORTAL_VERSION` lower than the baked version logs the skip, keeps
  the baked portal and completes the boot.

- **Prerelease and deny handling is tested.** On a new image: a pin to a version on the deny
  list keeps the installed portal and logs the reason; a failed deny check installs nothing; a
  hand run of a denied version exits non-zero with every file unchanged; a hand run above latest
  says the version is unpromoted before asking. In CI: a mutation that changes a file under
  `portal-aio/` without raising VERSION fails the check, a VERSION equal to a promoted bake of
  another tree fails CI, promotion and publish, and a `-dev.g` version built from an
  all-digit sha with a leading zero parses and sorts below its release.

- **The opt-outs are tested in every launch mode.** On a new image, the matrix boots with
  `PORTAL_UPDATE=false`, with `--no-update-portal`, and with both set alongside a
  `PORTAL_VERSION` pin, in SSH, Jupyter and entrypoint mode. Each must log the skip and make no
  release lookup or download.

- **The guide to writing a fix ships with the harness.** `docs/portal-first-boot-fixes.md` covers:
  - the module shape;
  - declaring targets and starting states;
  - superseding, and markers;
  - the record;
  - retirement;
  - the tests each fix needs.

  It is written alongside the harness, kept current with it, and linked from
  `docs/context-map.md`. The harness does not ship without it.

## Build status

The proof of concept builds the harness, the updater and the gate. What it does not yet build is
tracked here against the step that must not happen without it. A binding condition listed here is
not refused; it is scheduled, and the step waits for it.

- **M0, before any image is promoted from the branch** (that freezes the updater in every image
  and saved template that pins it): the ceiling and its deferral; the 30-minute backstop and the
  per-minute log line on the updater's own downloads; worker mode; the identity asset and the
  match that skips the download; `SKIP_ON` explicit on package fixes (L125); the updater's other
  environment overrides decided and documented, and the user-install plan refused when it cannot
  run unprivileged; per-release matrix cells for a new image (SSH and entrypoint launch), opted
  out, and as a serverless worker with its first-boot time reported; a one-off scripted run,
  recorded here, of GitHub unreachable, GitHub up with the package index down, a short ceiling
  that defers, and a throttled download; a live run of the final code on the support floor, in
  entrypoint mode, and through a user's own install and approval.
- **M1, before merge** (a merge that raises `VERSION` publishes the first gated release): the
  repository settings the gate depends on (immutable releases, a read-only default token, rulesets
  on all tags and on `portal-deny`, main protected) and the two environments (QA secrets; the
  release, with its first-promotion reviewer); the asset checked against the digest the platform
  reports, not the release notes; actions pinned by digest; the matrix's unauthenticated-access
  check made strict; the remaining opt-out cells (the flag, and each opt-out alongside a pin, in
  every launch mode); "current" resolved at run time and every vintage pinned and confirmed; the
  deny and one-tree checks at image promotion; demote rehearsed and safe with any tag; the
  emergency override; announcements and alerts; the canary; the guide to writing a fix; this ADR
  accepted.
- **M2, before the first package fix or supervisor action ships:** a hard deadline on the
  stage-65 hook, the dpkg repair at the start of every run, uv's checksum named in its row,
  supervisor results recorded, and a faster way to pull a package row that misbehaves upstream.
- **Before a second fix lands:** the retire and supersede refusals in the release workflow.

## Consequences

**Positive**
- **Reach without rebuilds.** A fix reaches every image vintage and saved template on its next new
  container.
- **A clear ownership line** for users and for us: we change a container at first boot, and never
  after.
- **A gate and a canary before the fleet**, using levers every frozen updater already honours, and
  a working demote.
- **Image promotion no longer waits on a portal release,** and no new image ever downgrades.
- **Two or more incidents between image builds are handled.** Each release is cumulative, tested as
  a whole, and skips work an image or container already has.
- **The registry stays bounded.** Fixes roll off on a published 36-month clock.
- **Less hand-written code per fix.**

**Accepted negative**
- **A bad change that passes QA is permanent** on every container that first-booted with it.
  Demote stops new first boots taking it, and the canary may catch it, but nothing reaches
  containers that already took it. The critical review's undo finding stays open by owner
  decision.
- **Some containers never receive a fix:**
  - those whose lookup failed;
  - those that pinned `PORTAL_VERSION`;
  - those that set `--no-update-portal`, or `PORTAL_UPDATE=false` on new images;
  - serverless workers on images built from 2025-08 until the rebuild that brings them inside
    the channel;
  - every container that has already booted.
- **On existing images, first-boot behaviour before stage 65 cannot be fixed by a release.** This
  covers the platform's `/.launch`, stages 05 to 55 and the frozen updater. A release changes these
  only from the next boot. Their first boot is fixed by rebuilding images.
- **Serverless workers on new images boot more slowly the first time.** The update and the harness
  add to a worker's first boot. Later starts are unaffected. The cost is measured on every release.
- **Releases are slower and cost QA instances.** A held release waits for its matrix, or for a
  recorded override.
- **Demote cannot pull a version back from images that baked it.** Those are fixed forward, or by
  repointing the image tag.
- **The portal stays on each vintage's own Python, back to 3.10, until a separate decision moves
  it.**
  Python 3.10 is past end of life. Dependencies that drop it are held at their last 3.10 release.
- **Images older than the support floor stop receiving retired fixes.** Their frozen updater still
  fetches every release.
- **The QA matrix is the main safety control for fleet-wide root changes.** A gap in its coverage
  is how a fleet-wide break would get through.
- **Building this adds invariants and lint rules:**
  - registry-not-glob;
  - early-slot standard-library-only;
  - every release runs on Python 3.10;
  - one owner per target;
  - every starting state tested;
  - a change under `portal-aio/` raises VERSION above every published version;
  - a version number names one tree;
  - `portal-aio/MANIFEST` is current.
- **A user's own install may wrap a binary they replaced.** This is narrowed by the container's
  history (Decision 5). Once a fix in a chain has run, finding its target "as shipped" means the
  user replaced it, and later fixes leave it alone. One case remains. The user replaced a file no
  fix had touched, then ran `update-portal` themselves, and the new release carries a first fix for
  that file. The file looks as it did at first boot, so the fix wraps it. That is accepted: it is
  their install, run at their request. The alternative was rejected as costly to keep correct:
  carrying the shipped sha256 of every target, for every vintage and architecture.

## What would reverse this

- A need to change containers after first boot, for example a fix that must reach running
  instances. That would mean dropping the ownership rule, and with it option B's trade.
- Evidence that the matrix misses breakages that reach customers. That would mean revisiting
  undo, or narrowing what the channel may deliver.
- Launch data showing images beyond the 36-month window are still widely launched. The window
  would then be extended for them, or the matrix would gain those vintages.
- A decision to move the portal to one newer Python on every vintage. The Python 3.10
  obligation in option L and the binding conditions would then be replaced by its terms.
- A new-image updater that verifies signatures before unpacking. Signing (option H) would then be
  worth adding for those images. Older images would still depend on publish control and the gate.

## Worked example: two fixes on one binary

This example is illustrative. Fix B and the version numbers are hypothetical.

**The two fixes.**
- **Fix A is `mptcp_off`** (ADR 0061), first shipped in v3.1.10. It moves the shipped `caddy` to
  `.mptcp_off/caddy` beside it. At the original path it writes a wrapper carrying A's marker, and
  the wrapper runs the original with `GODEBUG=multipathtcp=0`.
- **Fix B ships later, in v3.3.0.** Caddy now also needs a second Go runtime setting.
- **B supersedes A.** Both fixes change the same wrapper, so B declares
  `supersedes: [mptcp_off]`. v3.3.0's registry lists B, and no longer lists A.

**What each container ends up with.** A container runs one release's harness, the latest at its
first boot. It runs another only when the user installs one themselves.

| Container | Harness runs under | Fixes run | Result |
|---|---|---|---|
| Older image, first boot while v3.1.10 to v3.2.x is latest | that release | A | A's wrapper, permanently. It never receives v3.3.0 unless the user installs it. |
| Older image, first boot while v3.3.0 is latest | v3.3.0 | B | B's wrapper over the shipped binary. A never runs. |
| Took A at first boot; the user later installs v3.3.0 | v3.3.0 | B | B rewrites A's wrapper in place, once the user confirms: the prompt on new images, `portal-fixes apply` on older ones. |
| New image that bakes A in source | v3.3.0 | B | B adds only its own setting. |
| New image that bakes B in source | v3.3.0 | none | Already fixed. |
| The user replaced `caddy` and the harness later runs | any | that release's fix for `caddy` | See Consequences on a user's own install. |

**B's starting states.**

| State | How B recognises it | What B does |
|---|---|---|
| As shipped | `caddy` is a regular file with no harness marker, and the image's launch script does not export A's setting | Moves the binary to `.mptcp_off/caddy` and writes a wrapper with both settings, carrying A's and B's markers |
| A applied | the wrapper carries A's marker but not B's | Rewrites the wrapper with both settings, keeping A's marker; leaves `.mptcp_off/caddy` untouched |
| A baked | the image's launch script exports A's setting (the check L118 enforces) | Wraps the binary with B's setting only, appended to the value the launch script already sets |
| B baked | B's id is in the image's baked-fix list | Nothing. The harness skips B before it runs |
| B applied | the wrapper carries B's marker | Nothing. B reports "already applied" |
| Not ours | any other content, or a harness marker from a fix B does not know | Nothing. B records "not ours" and why |

**What this means for the record and later releases.**
- **The container's record shows each step.** A container that went from A to B lists A, then B.
  Each entry has its before and after hashes, so its whole history can be read from the record.
- **An older release installed later changes nothing.** Suppose a user then installs v3.1.10
  themselves. A finds its own marker in B's wrapper and reports "already applied".
- **A later fix C on the same wrapper extends the chain.** C declares
  `supersedes: [mptcp_off, B]`, handles every state in the table above plus B's own, and keeps all
  three markers.
