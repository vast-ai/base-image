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

sd-forge's provisioning scripts split `HF_MODELS`, `CIVITAI_MODELS`, `WGET_DOWNLOADS` and
`EXTENSIONS` on `;` in their own bash parser.

Vast drops any template variable whose value contains `;`. The platform stores the value,
then leaves it out of the container's environment, with no error. Verified on 2026-10-07:
`T_SEMI=a;b` was stored on the instance but absent from PID 1's environment. `a^b`, `a,b`
and `a|b` all arrived intact. So every list with two or more entries set in a template has
been silently missing, and only single-entry values worked. The platform fix is tracked
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
6. **`,` or `;` everywhere, with two comma exceptions shared by every variable.** Chosen.

## Decision

Every multi-entry variable listed above splits with one function,
`provisioner/envlist.py` `split_entries`:

- `,` or `;` separates entries. `|` still separates fields within an entry.
- A comma inside `[...]` does not separate, so pip extras stay whole
  (`transformers[torch,sentencepiece]`).
- A comma followed, after any spaces, by a version operator (`<`, `>`, `=`, `!=`, `~=`)
  does not separate, so version ranges stay whole (`torch>=2.4,<2.6`).
- Entries are trimmed, and empty entries are dropped. Callers that skipped `#` comments
  still do.

The documentation recommends `,` and says why. `;` keeps working. One kind of existing
value does change meaning: a single entry that contains a bare comma, such as a lone
`PROVISIONING_POST_COMMANDS` command with a comma in it, or a download URL with a raw
comma. It now splits. Values with `;` never reached instances from templates, so these
single entries are the only ones that worked before and behave differently now.

A manifest field typed `list[str]` that arrives as a string is split by the same rule. The
ComfyUI extension's `workflows:` setting does the same.

sd-forge's bash parser calls the same file rather than repeating the rule. Those scripts are
fetched by URL and can run on an image built before this change; there the splitter is
missing, and they fall back to splitting on `;`, which is the old behaviour.

## Binding conditions

1. **One implementation.** Lint rule L107 refuses a direct `.split(";")` in the provisioner or
   a provisioner extension, and an `IFS=';'` in a provisioning script outside its
   `split_env_entries` fallback. `downloaders/wget.py` is exempt, by name: it splits an HTTP
   `Content-Disposition` header.
2. **Tested where it ships.** The splitter, the convention variables, the `${VAR}` list
   fields, the ComfyUI extension and sd-forge's shipped parser each have tests, and each fix
   has a mutation that fails them. The ComfyUI extension's suite runs in CI (it had never
   run there).

## Consequences

- Lists set in templates reach the provisioner with commas today.
- `${VAR}` in a manifest list field works as people already wrote it.
- Accepted limits: a URL's own comma must be written `%2C`. A shell command that itself
  contains a comma cannot go in `PROVISIONING_POST_COMMANDS`; it belongs in a manifest's
  `post_commands` list or a script. A pip marker still cannot go in `PROVISIONING_PIP`,
  as before.
- Not changed here: `PORTAL_CONFIG` (split on `|`), and `AUTH_EXCLUDE`, `PORTAL_LINKS` and
  the other comma-separated portal variables.

## What would reverse this

- **Vast passes `;` through.** `,` stays supported regardless; the documentation can
  present both equally.
- **A common item type turns out to contain commas outside the two exceptions.** The
  exceptions are widened in `envlist.py`, the one place they live, rather than per variable.
