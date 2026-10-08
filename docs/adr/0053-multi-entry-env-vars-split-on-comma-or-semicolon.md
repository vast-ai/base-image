# ADR 0053 — Multi-entry env vars split on a comma or a semicolon

- **Status:** Accepted
- **Date:** 2026-10-07
- **Decision owner:** Rob Ballantyne

## Context

Several environment variables hold a list. The provisioner splits all of them on `;`:

- `PROVISIONING_DOWNLOADS`, `PROVISIONING_GIT_REPOS`, `PROVISIONING_APT`, `PROVISIONING_PIP`,
  `PROVISIONING_CONDA` and `PROVISIONING_POST_COMMANDS`;
- any variable a manifest names in `env_merge`;
- `PROVISIONING_COMFYUI_WORKFLOWS`, read by the ComfyUI extension.

sd-forge's provisioning scripts split `HF_MODELS`, `CIVITAI_MODELS` and `WGET_DOWNLOADS` on
`;` in their own bash parser. (Their in-script `EXTENSIONS` array has the env var's name and
replaces it, so that variable is never read. This change leaves that alone.)

Vast drops any template variable whose value contains `;`. The platform stores the value,
then leaves it out of the container's environment, with no error. Verified on 2026-10-07:
`T_SEMI=a;b` was stored on the instance but absent from PID 1's environment. `a^b`, `a,b`
and `a|b` all arrived intact, and so did `six>=1.16,<2,tomli-w` on 2026-10-08. So every
list with two or more entries set in a template has been silently missing, and only
single-entry values worked. The platform fix is tracked
separately, and may take time.

A second defect sat next to it. A manifest list field written as one string, such as
`apt_packages: "${APT}"` or `packages: "${PIP_PACKAGES}"`, reached the installers as a bare
string: pip received one argument per character, apt raised `TypeError`. The ComfyUI
extension's `workflows:` setting did the same. Env expansion substitutes text inside a
string and never produces a list.

`;` also splits values it should not: a pip environment marker (`pkg; python_version<"3.11"`)
and a shell command in `PROVISIONING_POST_COMMANDS` (`cd x; make`). No test covered either.

## Options considered

1. **Keep `;` and wait for the platform.** Rejected: lists in templates stay broken until
   then, with no signal to the user.
2. **Tell users to put lists in a `PROVISIONING_MANIFEST` file.** A manifest is not subject
   to the filter. Rejected as the only answer: it means hosting a file to install two
   packages.
3. **A different character per field** (`,` for apt, a comma that skips version ranges for
   pip, `;` only for commands). Rejected: a rule that changes between variables is hard to
   remember and document, and users copy values between fields.
4. **`^` as the delimiter everywhere.** It passes the filter and appears in no package, URL
   or git ref. Rejected: not a recognised list separator, and it reads badly in a template.
5. **`|`.** Rejected: it already separates fields within an entry (`url|dest|ref`). Moving
   the field separator would break every existing value.
6. **`,` or `;` in the same value, with two comma exceptions.** The first version of this
   change. Rejected in review: a value exported from an onstart script bypasses the
   platform filter, so live templates do pass `;` lists, and any entry in one with a bare
   comma (`echo a,b;touch y`, `awk -F, ...`, `mkdir /w/{a,b}`, a URL's own comma) would
   have split differently.
7. **`;` if present, otherwise `,` with two comma exceptions, the same for every
   variable.** Chosen: every existing `;` value keeps its exact meaning.

## Decision

Every multi-entry variable listed above splits with one function,
`provisioner/envlist.py` `split_entries`:

- A value containing `;` is split on `;` only, exactly as before. Every existing `;` list
  keeps its meaning, commas inside entries included.
- A value without `;` is split on `,`. `|` still separates fields within an entry.
- A comma inside `[...]` does not separate, so pip extras stay whole
  (`transformers[torch,sentencepiece]`).
- A comma followed, after any spaces, by a version operator (`<`, `>`, `=`, `!=`, `~=`)
  does not separate, so version ranges stay whole (`torch>=2.4,<2.6`).
- A `[` that is never closed within its entry (a typo, or a raw bracket in a URL) does
  not count: the text after it splits normally, and the text before it stays whole.
- Entries are trimmed, and empty entries are dropped. Callers that skipped `#` comments
  still do.
- The provisioner logs the parsed entries, not just how many, so a value split where the
  user did not mean it shows in the provisioning log. On these lines a download or git URL
  is shown without credentials, query or fragment; a URL the line cannot parse is shown as
  `<unparseable URL>` and fails its own download later, never the run. This does not make
  the log token-free: the downloaders and installers already log URLs, packages and
  commands as given.

The documentation recommends `,` for templates and says why. `;` keeps working unchanged.
One kind of existing value does change meaning: a value with **no** `;` holding a single
entry that contains a bare comma, such as a lone `PROVISIONING_POST_COMMANDS` command
with a comma in it, or a single download URL with a raw comma. It now splits. This is
accepted as an edge case. Set from onstart, such an entry can end with `;` to stay whole.
Set in a template it cannot, because the platform drops the `;`; it has to move to a
manifest's `post_commands`, a script, or (for a URL) `%2C`. No such value was found in
this repo's templates and manifests or in the published template sources.

A value written by two parties follows the same rule: a template's `a,b` with `;c`
appended from onstart has a `;`, so it splits on `;` alone and `a,b` stays one entry. The
documentation says not to mix the two in one value.

A manifest field typed `list[str]` that arrives as a string is split by the same rule,
except the command fields (`post_commands`, a git repo's `post_commands`, a service's
`pre_commands`). A command string is shell already, so splitting it could only break it:
`cd /x; make` would run `make` outside `/x`. A command field written as one string runs as
one command, and an empty one is no command. The `PROVISIONING_POST_COMMANDS` variable still splits, because it is defined
as a list of commands. The ComfyUI extension's `workflows:` setting is split like an item
field.

The rule ships in the base image, and derivatives pin a dated base, so two callers outside
the base must survive an older one:

- **The ComfyUI extension** ships in the derivative (AIO Studio copies it, and both
  images' built-in manifests load it). If `provisioner.envlist` is missing it falls back
  to the old `;` split and logs that it did, rather than failing to import, which would
  abort provisioning on every ComfyUI and AIO Studio instance.
- **sd-forge's bash parser** splits a value containing `;` with its original `read`,
  unchanged, so those values keep their exact meaning (multi-line values included). It
  runs the same file as the provisioner only for a value without `;`. The scripts are
  fetched by URL; where the splitter is missing or fails, they split on `,` plainly,
  which is safe for their lists of `URL|PATH` entries.

## Binding conditions

1. **One implementation.** Lint rule L107 refuses a `;` split (`.split`, `.rsplit`,
   `split(sep=...)`, `re.split`) in the provisioner or a provisioner extension, outside an
   extension's `def split_entries` fallback within `except ImportError:` (the old-base
   fallback above), and an `IFS=';'` in any
   provisioning script outside its `split_env_entries` function. `downloaders/wget.py` is
   exempt, by name: it splits an HTTP `Content-Disposition` header. A split written some
   other way is not caught; the rule is a fast check, not a proof.
2. **Tested where it ships.** The splitter, each convention variable's parser, the
   `${VAR}` item and command fields, the ComfyUI extension (including loading on a base
   without the splitter) and sd-forge's shipped parser (including a missing or failing
   splitter, and its `;` values compared with the parser from before this change) each
   have tests, and each fix has a mutation that fails them. The ComfyUI extension's suite
   runs in CI (it had never run there). On a live instance, `base/58-env-lists` checks
   that base-qa's comma-separated `PROVISIONING_PIP`, `PROVISIONING_POST_COMMANDS` and
   `PROVISIONING_DOWNLOADS`, set as real template env vars, were each parsed into the
   expected entries (from the provisioning log) and applied; it is required in all three
   copies of base-qa's required list. Its pip entries are already installed in the base,
   so they need no package index. Its two downloads come from GitHub, pinned to a
   commit: this required test accepts a GitHub outage as a reason to hold a base release,
   unlike `13-provisioner-selftest`, which stays off the network.
3. **Released after the base.** Base and pytorch are built, QA'd and promoted from the
   branch before merging. Each derivative gets commas when its base pin moves to a base
   that has the splitter; until then a comma-separated value is read as one entry. The
   documentation says so. The exception is sd-forge's scripts: they are fetched by URL,
   so their change goes live for every sd-forge image at merge, and is reverted by
   reverting it on main.

## Consequences

- Lists set in templates reach the provisioner with commas, on images built after this
  change.
- `${VAR}` in a manifest list field works as people already wrote it.
- Accepted limits: a URL's own comma must be written `%2C`. A shell command that itself
  contains a comma cannot go in `PROVISIONING_POST_COMMANDS`; it belongs in a manifest's
  `post_commands` list or a script. A pip marker still cannot go in `PROVISIONING_PIP`,
  as before.
- Once templates use commas, reverting this breaks them again. Problems are fixed forward.
- Not changed here: `PORTAL_CONFIG` (split on `|`), and `AUTH_EXCLUDE`, `PORTAL_LINKS` and
  the other comma-separated portal variables.

## What would reverse this

- **Vast passes `;` through.** `,` stays supported regardless; the documentation can
  present both equally.
- **A common item type turns out to contain commas outside the two exceptions.** The
  exceptions are widened in `envlist.py`, the one place they live, rather than per variable.
