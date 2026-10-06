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
4. **`${value@Q}`.** Exactly the right output, but it needs bash 4.4 or newer.
   `Dockerfile.extend` wraps third-party bases whose bash we don't control. Rejected.
5. **Single quotes, with `%q` for control characters (chosen).** `NAME='value'`, with
   each `'` written as `'\''`. A value without a control character comes back
   byte-for-byte. A value with a newline or tab is written as `$'...'` on one line.
   This is portable to any bash.

## Decision

`_vast_dump_env` writes each launch variable as `NAME='value'`, or as `NAME=$'...'`
when the value contains a control character. Sourcing the file restores every value
exactly as Docker passed it: nothing is expanded and nothing runs. Names that are not
shell identifiers are skipped, because sourcing cannot assign them and their text
would be run as a command.

## Binding conditions

- Every value round-trips byte-for-byte in both the boot shell and a fresh login
  shell, generated secrets containing `$` included, and nothing in a value runs.
  `tools/imagegen/tests/test_prep_env_sh.py` checks this against the shipped file. It
  catches the old `"%s"` quoting, the `$NAME`-expanding variant (option 2), and an
  unescaped embedded `'`.
- Each variable stays on one line starting `NAME=`. ADR 0014's `_vast_user_set`
  depends on this.

## Consequences

- Template values reach every process as written, matching what Docker gives PID 1.
- **Behaviour change:** a value containing `$VAR` is no longer expanded when the file
  is sourced. No template or doc that relies on this was found (the
  `-e HF_TOKEN=$HF_TOKEN` examples are expanded by the user's local shell before the
  request is sent). Users who want it put the line in `${WORKSPACE}/.env`, which is
  still sourced as shell. The README says so.
- Values that used to run code or break (`$(...)`, backticks, `"`, newlines) are now
  literal. That only changes values that were already broken.
- `pam_env` also reads `/etc/environment`. It strips one pair of matching quotes, so
  ordinary values read the same as before. A value containing `'` or a control
  character was already garbled there and still is.

## What would reverse this

Evidence that templates in use depend on in-container `$VAR` expansion of launch env
values. Even then, it would come back as an explicit opt-in, never as the default,
because of the secret-truncation case in option 2.
