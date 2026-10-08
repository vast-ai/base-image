# ADR 0052 — the launch environment is written to /etc/environment literally

- Status: accepted
- Date: 2026-10-06
- Decision owner: Rob Ballantyne
- Related: ADR 0014 (`12-cpu-thread-limits.sh` finds user variables by `^NAME=`)

## Context

At first boot `vast_boot.d/10-prep-env.sh` dumps the launch environment into
`/etc/environment`. The boot shell sources that file with `set -a`, and so does every
login shell (`45-user-write-bashrc.sh`) and every supervisor script (`utils/environment.sh`).
The file is therefore the environment most of the instance actually sees, not the one
Docker handed to PID 1.

Two other readers parse the same file without a shell. `pam_env` builds the environment
for non-interactive SSH sessions (`ssh host cmd`, rsync, scp, remote IDEs) and for `sudo`;
linux-desktop's `export_env.sh` builds the desktop session's. Neither expands or
unescapes anything. pam_env strips a pair of surrounding quotes and cuts the line at its
first `#`. The desktop parser exports a value only if it is wholly `'...'` or `"..."`
with no inner quote of the same kind, or bare, and skips any other line.

The dump wrote each variable as `NAME="value"`, with the value unescaped. Inside
double quotes bash still evaluates `$`, backticks and `\`, and a `"` ends the string:

| Launch value | What sourcing produced |
|---|---|
| `a;b` | `a;b` (literal; `;` is harmless inside `"..."`) |
| `p4$sW0rd` (a generated password) | `p4` |
| `abc$1xyz` | `abcxyz` |
| `` `cmd` `` or `$(cmd)` | the output of `cmd`, which runs at every boot and every login |
| `say "hi"` | `say hi` |
| `say "hi there"` | empty, and `there` runs as a command |
| a value containing a newline | extra lines; a line shaped like `NAME=...` becomes its own variable |
| `$WORKSPACE/models` | the expanded path |

Docker passes these values through unchanged, so the image was the only layer altering
them, and PID 1 saw a different value from every other process in the instance. The
Vast platform currently filters some characters out of template env values, and that
filter is expected to be loosened. Values containing `;`, `\` and backticks would then
reach this dump, which needs to be correct before that happens.

## Options considered

1. **Keep `"..."` and escape `\ " \``.** `$(...)`, `$((...))` and `${VAR:-$(cmd)}`
   would still run, and every `$` in a secret would still be read as a reference.
   Rejected.
2. **Keep plain `$NAME` / `${NAME}` references as a feature, everything else literal.**
   This was built and tested: a tokenizer kept references live, escaped everything
   else, and used `\$` for a literal `$`. It was rejected because it leaves silent
   corruption exactly where it hurts most. A generated secret containing `$` followed
   by a letter or underscore (`p4$sW0rd`, `Xk9$_aZ`) is still cut short (`p4`, `Xk9`).
   Nothing points the user at the env, and `\$` only helps users who know to escape a
   value a generator gave them. The benefit (`MODEL_DIR=$WORKSPACE/models`) is a
   convenience that `${WORKSPACE}/.env` and provisioning scripts already provide.
3. **`printf %q` for every value.** Correct and single-line, but ordinary values come
   out with backslash escapes (`a\;b`, `my\ value`), which is hard to read in a file
   users are told they can edit. Rejected as the default; used for the
   control-character case.
4. **`${value@Q}`.** Right for bash, but it needs bash 4.4 or newer, and external
   images run these stages on upstream bases whose bash we don't pick
   (`tools/convert-non-vast-image.sh`). It also writes every value containing `'` in
   a form the non-shell readers misread. Rejected.
5. **Single quotes for every value.** Right for bash, but a value containing `'` (for
   example `it's`) is written `'it'\''s'`, which pam_env and the desktop parser read
   back as `it'\''s`. The old double-quoted format gave them `it's`. Rejected: it
   regresses those readers for an ordinary value.
6. **A second file in shell syntax, `/etc/environment` left for the other readers.**
   Rejected: `/etc/environment` is a documented interface. Users edit it on a running
   instance and restart services, and `12-cpu-thread-limits.sh` edits lines in it. Two
   files would drift apart on the first edit, and nothing would bring them back in line.
7. **One file, with each value quoted the way every reader agrees on (chosen).**

## Decision

`_vast_dump_env` picks, per value, the quoting every reader of the file agrees on:

| Value | Written as | bash | pam_env | desktop parser |
|---|---|---|---|---|
| no `'` | `NAME='value'` | exact | exact | exact |
| a `'`, but no `$` `` ` `` `"` `\` | `NAME="value"` | exact | exact | exact |
| a `'` with one of `$` `` ` `` `"` `\` | `NAME='it'\''s'` | exact | raw quoted text | not set |
| a control character (newline, tab) | `NAME=$'...'` | exact | raw quoted text | not set |

pam_env also cuts every value at its first `#`, whatever the quoting; the old format had
the same cut, and no quoting avoids it.

Bash, which boots the instance and starts every service, restores every value exactly
as Docker passed it: nothing is expanded and nothing runs. The other readers get every
value right except the last two rows, which no single form can serve for both; those
values were already broken in the old format, for bash as well. The `'` replacement is
held in a variable, so bash 3.2 and 4.2, which keep a replacement's backslashes inside
a double-quoted `${//}`, write the same file. Names that are not shell identifiers are
skipped, because sourcing cannot assign them and their text would be run as a command.

The `_VAST_*_LIB_ONLY` variables that let tests load a stage's functions without
running it are unset by `boot_default.sh` before the stages run, so a template cannot
skip a stage by setting one.

## Binding conditions

- Every value round-trips exactly in both the boot shell and a fresh login shell,
  generated secrets containing `$` included, and nothing in a value runs.
  `tools/imagegen/tests/test_prep_env_sh.py` checks this against the shipped file. It
  catches the old `"%s"` quoting, the `$NAME`-expanding variant (option 2), and an
  unescaped embedded `'`, all on the host bash. It also reads the file the way pam_env
  does and through the real linux-desktop parser (every value outside the last two rows
  must come back exact, and the desktop parser must skip the rest), runs the dump under
  bash 3.2 and 4.2 in docker (a missing docker fails in CI rather than skipping), checks
  that `boot_default.sh` unsets every stage's lib-only switch, and checks that the boot
  log notice names variables and never prints a value.
- On a live instance, `base/57-env-literal` reads probe values back from a fresh
  shell, supervisord and caddy (not caddy on a serverless worker, which does not start
  it), and checks that none of them ran. It is required to
  pass in all three copies of base-qa's required list (the template, the promote
  workflow's QA job and its summary arbiter), which `test_promote_gate_wiring.py`
  holds together. base-qa exports the probes from its onstart, which runs before the image boots,
  so they reach the dump the way template env vars do, without the platform's env
  character filter.
- Each variable stays on one line starting `NAME=`. ADR 0014's `_vast_user_set`
  depends on this.

## Consequences

- Template values reach every process as written, matching what Docker gives PID 1.
- **Behaviour change:** a value containing `$VAR` is no longer expanded when the file
  is sourced. No template or doc in this repo relies on this (the
  `-e HF_TOKEN=$HF_TOKEN` examples are expanded by the user's local shell before the
  request is sent), but templates outside it can't be searched. So the boot log names
  each variable whose value contains `$NAME` text (the name only; values can be
  secrets). Users who want expansion put the line in `${WORKSPACE}/.env`, which is
  still sourced as shell. The README says so.
- Values that used to run code or break (`$(...)`, backticks, `"`, newlines) are now
  literal. That only changes values that were already broken.
- pam_env and the desktop parser read every value the old format gave them correctly,
  except a value containing `'` together with `$` `` ` `` `"` or `\`, or a control
  character. pam_env now gets the raw quoted text for those, and the desktop session
  does not get the variable from this file; in the old format they were wrong for bash
  as well, and could run as code. pam_env's cut at `#` is unchanged.
- Not changed here: the dump's `grep -z` drops a value that is not valid UTF-8 when it
  runs under a UTF-8 locale.

## What would reverse this

Evidence that templates in use depend on in-container `$VAR` expansion of launch env
values. Even then, it would come back as an explicit opt-in, never as the default,
because of the secret-truncation case in option 2.
