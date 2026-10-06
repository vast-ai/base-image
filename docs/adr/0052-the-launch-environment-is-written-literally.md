# ADR 0052 — the launch environment is written literally, except plain variable references

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
| `$WORKSPACE/models` | the expanded path |
| `` `cmd` `` or `$(cmd)` | the output of `cmd`, which runs at every boot and every login |
| `say "hi"` | `say hi` |
| `say "hi there"` | empty, and `there` runs as a command |
| a value containing a newline | extra lines; a line shaped like `NAME=...` becomes its own variable |

Docker passes these values through unchanged, so the image was the only layer altering
them. Nobody designed the expansion, but one part of it is useful: a template can
build one value from another (`MODEL_DIR=$WORKSPACE/models`). The rest is a hazard.
The Vast platform currently filters some characters out of template env values, and
that filter is expected to be loosened. Values containing `;`, `\` and backticks would
then reach this dump, which needs to be correct before that happens.

## Options considered

1. **Fully literal (`NAME='value'`).** Simplest and exact, but it removes `$VAR`
   expansion that templates may already use, and it throws away a useful feature.
   Rejected.
2. **Keep `"..."` and escape only `\ " \``.** `$(...)`, `$((...))` and `${VAR:-$(cmd)}`
   would still run, and `${VAR@P}` runs command substitution held in another
   variable's value. Allowing only "safe" `${...}` operators means re-implementing
   bash's parameter-expansion grammar. Rejected.
3. **`${value@Q}`/`printf %q` plus a separate expansion pass.** Expanding a second
   time at write time resolves references against the boot environment instead of the
   environment that sources the file, and needs bash 4.4+ for `@Q`. `Dockerfile.extend`
   wraps third-party bases whose bash we don't control. Rejected.
4. **Double quotes, with only `$NAME` and `${NAME}` left live (chosen).** A small
   tokenizer copies plain text through, keeps a reference to another variable as it
   is, and backslash-escapes every other `\`, `"`, backtick and `$`. `\$` in the value
   gives a literal `$`, which is what the old format also did. It is portable to any
   bash. Values with a control character are written fully literal with `%q` (`$'...'`)
   so they stay on one line.

## Decision

`_vast_dump_env` writes each launch variable as `NAME="..."` built by
`_vast_env_quote`, or as `NAME=$'...'` when the value contains a control character.
When sourced:

- `$OTHER` and `${OTHER}` expand to that variable's value, or to empty if it is unset.
  This is a documented feature (README, "Referencing Other Variables").
- Everything else is literal: `;`, quotes, backticks, `\`, `$(...)`, `$((...))`,
  special parameters (`$1`, `$$`, ...) and every `${...}` form beyond a bare name.
- `\$` is a literal `$`.
- A reference to the variable itself stays literal. Docker has already replaced the
  value it would extend, so expanding it would produce `/x:/x:$NAME`.
- Names that are not shell identifiers are skipped, because sourcing cannot assign
  them and their text would be run as a command.

## Binding conditions

- Nothing in a value runs, and every value other than a plain reference comes back
  byte-for-byte. `tools/imagegen/tests/test_prep_env_sh.py` checks this against the
  shipped file. Separate test failures catch each of these mutations: the old
  unescaped `"%s"`, fully literal quoting (the expansion feature lost), an unescaped
  `$`, and an allowed self-reference.
- Each variable stays on one line starting `NAME=`. ADR 0014's `_vast_user_set`
  depends on this.

## Consequences

- Template values reach services as written, plus the documented reference expansion.
- Values that used to break or run code (`$(...)`, backticks, `"`, newlines) are now
  literal. That is a behaviour change only for values that were already broken.
- A reference resolves against the environment of the shell that sources the file. In
  the boot shell that is the launch env; in a fresh shell it is the lines above plus
  whatever that shell inherited. References are one level deep.
- PID 1 still sees the unexpanded text. That was already true before this change.
- `pam_env` also reads `/etc/environment`. It strips one pair of matching quotes and
  does not unescape, so a value containing an escaped character reads differently
  there. Those values were already garbled for `pam_env` before.
- Quoting is a bash loop over special characters: about 2.5 s for a pathological
  30 KB value, and negligible for normal ones.

## What would reverse this

Real templates needing more of bash's expansion (defaults like `${VAR:-x}`, for
example). Adding a specific operator would be a deliberate change to this ADR, with a
test showing that it cannot run code.
