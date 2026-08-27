"""Guard: the promotion Slack headline must describe what HAPPENED.

On 2026-08-14 a QA cell drew a host with no GPU. The required-pass gate did its
job — three GPU tests skipped, `skip != pass`, the cell blocked, and `promote`
was skipped by transitive failure. Nothing was promoted. Slack said:

    Base image promoted — 1 auto tag(s) HELD (QA did not clear them);
    dated tags landed as normal

Every clause of that is false: nothing was promoted, no dated tag landed, and
the "HELD" framing implies the other tags moved. Both arms of the headline
expression opened with "Base image promoted" unconditionally and never consulted
`needs.promote.result`.

That is worse than no notification. Slack is the only place most people look, so
a run where the gate correctly STOPPED a promotion was announced as one where it
let a partial promotion through — inverting the safety signal.
"""
import re

import pytest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
PROMOTE = REPO / ".github" / "workflows" / "promote-base-image.yml"


def _notify_with() -> dict:
    data = yaml.safe_load(PROMOTE.read_text())
    return data["jobs"]["notify"]["with"]


def test_notify_depends_on_promote():
    """The headline cannot consult a job it does not depend on."""
    needs = yaml.safe_load(PROMOTE.read_text())["jobs"]["notify"]["needs"]
    assert "promote" in needs, "notify must depend on promote to report its result"


def test_headline_is_an_ALLOWLIST_on_success():
    """Only a successful promote may open with "promoted".

    The first fix enumerated the bad outcomes ('skipped', 'failure') and let
    everything else fall through to "Base image promoted". A CANCELLED run is
    neither, so on 2026-08-14 Slack announced "Base image promoted — 10 auto
    tag(s) HELD" for a run whose promote job never executed. Enumerating the ways
    a thing can go wrong misses the one nobody listed; requiring the single way it
    goes right cannot be missed.
    """
    headline = " ".join(str(_notify_with()["headline"]).split())
    assert "needs.promote.result != 'success'" in headline, (
        "the headline must key on promote SUCCEEDING, not on a list of known "
        "failure states — an unlisted state falls through to claiming a promotion")


@pytest.mark.parametrize("result", ["skipped", "failure", "cancelled", "some_future_state"])
def test_no_non_success_result_can_claim_a_promotion(result):
    """Evaluate the real expression's leading branch for each result GitHub can
    produce, including one it does not produce yet."""
    headline = " ".join(str(_notify_with()["headline"]).split())
    m = re.search(r"needs\.promote\.result != 'success'\s*&&\s*format\('([^']+)'", headline)
    assert m, "no not-promoted branch keyed on != success"
    msg = m.group(1)
    assert "NOT promoted" in msg
    assert not msg.startswith("Base image promoted"), msg
    assert "{0}" in msg, "the not-promoted headline should name the actual result"


def test_a_successful_promote_can_still_report_holds():
    """The allowlist must not flatten the useful case: a real promotion that held
    some tags still needs to say so."""
    headline = " ".join(str(_notify_with()["headline"]).split())
    assert "auto tag(s) HELD" in headline
    assert "needs.qa-summary.outputs.holds" in headline


def test_status_is_warning_when_promote_did_not_succeed():
    status = str(_notify_with()["status"])
    assert "needs.promote.result != 'success'" in status, (
        "a non-successful promote must not render as a normal green notification")


# ---- the cloudflared contract must actually run at build time --------------

BUILD = REPO / ".github" / "workflows" / "build-base-image.yml"


def _build_jobs():
    return yaml.safe_load(BUILD.read_text())["jobs"]


def test_the_build_runs_the_cloudflared_contract():
    """The Dockerfile fetches cloudflared unpinned, so the contract test IS the
    control. It has to run where the binary changes — on a rebuild — not only on
    a portal-aio/** push, or the unpinned fetch is unguarded between releases."""
    jobs = _build_jobs()
    assert "cloudflared-contract" in jobs, (
        "build-base-image.yml does not run the cloudflared contract test; the "
        "unpinned releases/latest fetch would ship unvalidated")
    run = " ".join(str(s.get("run", "")) for s in jobs["cloudflared-contract"]["steps"])
    assert "test_cloudflared_contract.py" in run


def test_the_contract_fans_out_over_ARCHITECTURE_ONLY():
    """Two competing constraints, and the matrix has to satisfy both.

    Dockerfile fetches `cloudflared-linux-${TARGETARCH}`, so amd64 and arm64 are
    different release artifacts — testing one says nothing about the other, and a
    release where arm64 lags would ship unguarded. But Cloudflare rate-limits
    quick-tunnel creation, so a cell per config x python (12 x 5) would
    rate-limit itself and then report the rate limit as a defect.

    The matrix must therefore be over arch and nothing else.
    """
    job = _build_jobs()["cloudflared-contract"]
    matrix = (job.get("strategy") or {}).get("matrix")
    assert matrix, "the contract job must fan out over architecture"
    assert set(matrix) == {"arch"}, (
        f"the contract matrix must be architecture ONLY, got dimensions {sorted(matrix)} "
        "— any other dimension multiplies quick-tunnel creations into Cloudflare's "
        "rate limiter")
    assert set(matrix["arch"]) == {"amd64", "arm64"}, (
        f"both shipped architectures must be covered, got {matrix['arch']}")
    assert len(matrix["arch"]) <= 2


def test_the_arm64_cell_can_actually_execute_its_binary():
    """An arm64 ELF does not run on an amd64 runner without binfmt. Without QEMU
    the cell would fail on exec and read as a contract break."""
    steps = _build_jobs()["cloudflared-contract"]["steps"]
    qemu = [s for s in steps if "setup-qemu" in str(s.get("uses", ""))]
    assert qemu, "the arm64 cell has no QEMU/binfmt setup; it cannot exec the binary"
    assert "arm64" in str(qemu[0].get("if", "")), (
        "QEMU should be conditional on the arm64 cell")
    extract = " ".join(str(s.get("run", "")) for s in steps)
    assert "--platform linux/${{ matrix.arch }}" in extract, (
        "the extract must pull the per-arch binary, not always amd64")


def test_the_contract_result_reaches_the_notification():
    """A `needs:` edge is not a control by itself. This rendered green from
    needs.build.result alone, so a failing contract was invisible: the staging
    tags are pushed upstream by `build` and promote is a separate dispatch."""
    notify = _build_jobs()["notify"]
    assert "cloudflared-contract-status" in notify["needs"]
    rendered = " ".join(str(v) for v in notify["with"].values())
    assert "needs.cloudflared-contract-status.outputs.state" in rendered, (
        "the contract state must reach the notification's rendered status, not "
        "merely appear in its needs")


def test_a_run_that_PROVED_NOTHING_cannot_render_as_a_pass():
    """The gate's normal degraded outcome is `skip`, and pytest exits 0 on it.

    Measured under a real Cloudflare rate limit: `3 passed, 3 skipped`, exit 0 —
    where the three that passed introspect the portal's own argv and say nothing
    about the binary. Reading the job result therefore rendered a green tick over
    a run that verified nothing, which is how this gate became decoration the
    first time. The state must be derived from which live assertions EXECUTED.
    """
    jobs = _build_jobs()
    run = " ".join(str(s.get("run", "")) for s in jobs["cloudflared-contract"]["steps"])
    assert "--junitxml" in run, (
        "the contract step must emit a junit report; an exit code cannot "
        "distinguish 'verified' from 'never asked'")
    assert "classify_contract_run.py" in run, (
        "something must classify the report into verified/unverified/broken")

    rendered = " ".join(str(v) for v in jobs["notify"]["with"].values())
    assert "'unverified'" in rendered, (
        "the notification must handle the unverified state explicitly; without "
        "it, a rate-limited run is indistinguishable from a verified one")
    assert "warning" in rendered, (
        "an unverified contract must render as ⚠️, not as a green build")


def test_an_ordinary_build_failure_is_not_reported_as_a_tunnel_problem():
    """`cloudflared-contract` needs `merge-manifests`, so any upstream failure
    SKIPS it. Keying the headline on `result != 'success'` therefore printed
    "the tunnel binary is UNVALIDATED" over what was a compile error — on every
    red build, which trains the reader to discount the one headline that matters.
    """
    jobs = _build_jobs()
    agg = jobs["cloudflared-contract-status"]
    body = " ".join(str(s.get("run", "")) for s in agg["steps"])
    assert "not-run" in body, (
        "the aggregate must have a state for 'the contract never ran because "
        "something upstream failed', distinct from 'the contract failed'")
    assert "skipped" in body and "cancelled" in body, (
        "a skipped or cancelled contract job is an upstream failure, not a "
        "tunnel defect")

    rendered = " ".join(str(v) for v in jobs["notify"]["with"].values())
    # The headline must be keyed on the POSITIVE states that warrant it. If it
    # were keyed on "anything that is not verified", not-run would drag a tunnel
    # claim onto an ordinary build failure again.
    assert "state == 'broken'" in rendered, (
        "the do-not-promote headline must fire on 'broken' specifically")
    assert "state != " not in rendered, (
        "keying on 'not this state' is what put a tunnel claim on a compile "
        "error; enumerate the states that warrant the headline")


def test_the_aggregate_is_severity_ordered_and_fails_closed():
    """Per-arch states have to collapse to one value for the notification, and
    the collapse must not be optimistic: one architecture proving nothing is not
    a pass, and a missing report is not a pass either."""
    agg = _build_jobs()["cloudflared-contract-status"]
    body = " ".join(str(s.get("run", "")) for s in agg["steps"])
    # Key on the grep PATTERNS, not on the words: "broken" and "unverified" both
    # appear in this step's own comments, so an index comparison on the bare
    # words passed no matter which order the branches were in — a guard reading
    # the prose that describes the fix instead of the fix.
    broken_at = body.index("'^broken$'")
    unver_at = body.index("'^unverified$'")
    assert broken_at < unver_at, (
        "broken must be checked before unverified, or a broken arch alongside "
        "an unverified one would report the milder state")
    assert agg["if"].startswith("always()"), (
        "the aggregate must run even when the contract job failed, or a broken "
        "contract produces no state at all")


def test_the_extracted_binary_is_asserted_to_be_the_right_ARCHITECTURE():
    """`file ... || true` printed the architecture and checked nothing. If
    --platform ever resolved to the amd64 image, the arm64 cell would re-test
    amd64 and report a green tick for coverage it never had."""
    run = " ".join(str(s.get("run", ""))
                   for s in _build_jobs()["cloudflared-contract"]["steps"])
    assert "aarch64" in run, "nothing asserts the arm64 cell got an aarch64 binary"
    assert "file ./cloudflared || true" not in run, (
        "`|| true` makes the architecture check unconditional decoration")


def test_the_per_PR_suite_does_not_spend_real_TUNNELS():
    """The live tests create real trycloudflare tunnels against a per-IP quota.
    Running them on every portal-aio PR spends that quota on `releases/latest`,
    which is not the binary that ships — and leaves the build-time run, which
    tests what DOES ship, rate-limited and unable to prove anything."""
    pr = yaml.safe_load((REPO / ".github" / "workflows" / "portal-aio-tests.yml").read_text())
    runs = " ".join(str(s.get("run", ""))
                    for j in pr["jobs"].values() for s in j.get("steps", []))
    assert 'not live' in runs, (
        "the per-PR portal suite must deselect the live cloudflared tests")


def test_the_extract_is_filter_aware():
    """build-base-image.yml takes a FILTER input. Reading configs[0] would look
    for a tag a FILTERed dispatch never built and go red for a non-contract
    reason; the merge matrix is what this run actually produced."""
    steps = _build_jobs()["cloudflared-contract"]["steps"]
    run = " ".join(str(s.get("run", "")) for s in steps)
    # Assert on the FILE, not on the substring "configs[0]" — the step's own
    # comment explains why it does not read configs[0], so a substring check
    # matches the prose that documents the fix. (Same shape as a linter rule
    # firing on the docstring describing it.)
    assert "configs/base-image.json" not in run, (
        "the extract reads the static config table; a FILTERed dispatch would "
        "send it looking for a tag that was never built")
    assert "MERGE_MATRIX" in run, (
        "the extract must derive its tag from the run's merge matrix — what this "
        "run actually produced")


def test_the_contract_tests_the_binary_that_SHIPPED():
    """releases/latest can move between the build and the check. Re-downloading
    would test a different binary from the one in the image."""
    run = " ".join(str(s.get("run", ""))
                   for s in _build_jobs()["cloudflared-contract"]["steps"])
    assert "docker cp" in run and "/opt/portal-aio/tunnel_manager/cloudflared" in run, (
        "the job must extract cloudflared from the built image, not re-fetch it")
    assert "CLOUDFLARED_BIN=" in run, "the extracted binary must be handed to the test"


def test_EVERY_artefact_stage_must_pass_before_the_build_reports_success():
    """`build-result` read `needs.build.result` alone — the build cells — and
    ignored the stages that turn them into shippable tags.

    Observed live: a GitHub 429 fetching the setup-crane action failed one
    config's manifest merge, which skipped both mini stages. The run's conclusion
    was `failure`, one config had no multi-arch manifest and every mini image was
    missing — and Slack said "Base Image Build Successful".
    """
    notify = _build_jobs()["notify"]
    for stage in ("merge-manifests", "build-mini", "merge-mini-manifests"):
        assert stage in notify["needs"], (
            f"notify cannot see {stage}, so it cannot report on it")

    rendered = " ".join(str(v) for v in notify["with"].values())
    for stage in ("merge-manifests", "build-mini", "merge-mini-manifests"):
        assert f"needs.{stage}.result" in rendered, (
            f"{stage} is in needs but never reaches the rendered status — a "
            f"needs edge alone is not a control")


def test_the_build_verdict_is_an_ALLOWLIST_of_passing_states():
    """The state nobody thought of has to land on the failing side. Enumerating
    bad states is how a cancelled promote once reported a promotion, and how a
    skipped contract job once reported a tunnel defect."""
    rendered = " ".join(str(_build_jobs()["notify"]["with"]["build-result"]).split())
    assert '["success","skipped"]' in rendered, (
        "the passing states must be enumerated explicitly as an allowlist")
    # Assert the POSITIVE form directly rather than blocklisting operators.
    # First attempt at this guard listed the forbidden comparisons ("== 'failure'",
    # "!= 'success'", ...) and a mutation to `!= 'failure'` sailed through it —
    # the guard against enumeration was itself enumerating. Requiring the one
    # correct spelling cannot be evaded that way.
    assert "needs.build.result == 'success'" in rendered, (
        "the build stage must be required to SUCCEED explicitly; any 'not-bad' "
        "spelling lets an unlisted state fall through to reporting success")
    assert rendered.rstrip().endswith("&& 'success' || 'failure' }}"), (
        "the expression must resolve positively: all-good -> success, anything "
        "else -> failure")


def test_skipped_is_tolerated_because_a_FILTERed_dispatch_empties_the_mini_matrix():
    """build-mini is `if: !inputs.DRY_RUN` over a matrix that a FILTER can empty,
    so `skipped` is a legitimate outcome and must not red the build."""
    rendered = str(_build_jobs()["notify"]["with"]["build-result"])
    assert '["success","skipped"]' in rendered, (
        "a FILTERed dispatch that skips the mini stages must still report green")


def test_a_cell_that_DIED_WITHOUT_REPORTING_is_not_a_cell_that_passed():
    """Observed live on 2026-08-17: GitHub 429/503'd the action download, so the
    arm64 contract cell failed at "Set up job" before any step ran and uploaded
    no artifact. amd64 uploaded `verified`. The aggregate saw one file, found no
    bad state in it, and reported `verified` — for a run where an entire
    architecture was never tested.

    Handling ZERO artifacts is not enough; PARTIAL is the dangerous case, because
    absence of contradiction reads as agreement.
    """
    agg = _build_jobs()["cloudflared-contract-status"]
    body = " ".join(str(s.get("run", "")) for s in agg["steps"])
    assert "EXPECTED_ARCHES" in str(agg["steps"]) or "EXPECTED_ARCHES" in body, (
        "the aggregate must know how many architectures owe it a result")
    assert "-lt" in body, (
        "the aggregate must compare the number of reports against the number of "
        "architectures, or a missing cell reads as a passing one")
    assert "CONTRACT_RESULT" in body and '!= "success"' in body, (
        "a contract job that did not succeed must not yield `verified`")


def test_the_aggregate_expects_every_arch_in_the_matrix():
    """EXPECTED_ARCHES is a literal, so it can drift from the matrix it describes.
    Adding a third architecture without bumping it would let that arch go
    unreported and still read as verified."""
    jobs = _build_jobs()
    arches = jobs["cloudflared-contract"]["strategy"]["matrix"]["arch"]
    step = [s for s in jobs["cloudflared-contract-status"]["steps"]
            if "EXPECTED_ARCHES" in str(s.get("env", {}))]
    assert step, "the aggregate declares no expected architecture count"
    assert int(step[0]["env"]["EXPECTED_ARCHES"]) == len(arches), (
        f"EXPECTED_ARCHES={step[0]['env']['EXPECTED_ARCHES']} but the contract "
        f"matrix has {len(arches)} architectures {arches}")


# ---- the syncthing version is resolved ONCE, not once per cell ---------------

def test_syncthing_version_is_resolved_once_and_passed_to_every_cell():
    """`Dockerfile` used to call api.github.com from inside EVERY build cell.

    Unauthenticated that endpoint allows 60 req/hr per IP and GitHub-hosted
    runners share a NAT pool, so 24 cells is a coin flip on a busy day — measured
    2026-08-17: HTTP 403 on cuda-13.0, which skipped all twelve manifest merges.

    The consistency argument is the stronger one: cells resolving `latest`
    independently means a release landing mid-build ships some configs on the old
    version and some on the new, with nothing recording which.

    If this wiring is removed the build still works, silently, by falling back to
    per-cell lookups — so nothing but this test would notice.
    """
    jobs = _build_jobs()
    assert "syncthing-version" in jobs["generate-matrix"]["outputs"], (
        "generate-matrix must publish one resolved version for the whole run")

    steps = jobs["generate-matrix"]["steps"]
    resolver = [s for s in steps if s.get("id") == "syncthing"]
    assert resolver, "no step resolves the syncthing version once"
    assert "GH_TOKEN" in str(resolver[0].get("env", {})), (
        "the resolve step must be AUTHENTICATED (5000/hr), or it inherits the "
        "same 60/hr limit it exists to escape")

    build = jobs["build"]
    assert "syncthing-version" in str(build["steps"]), (
        "the resolved version never reaches the build job")
    run = " ".join(str(s.get("run", "")) for s in build["steps"])
    assert "--build-arg SYNCTHING_VERSION=" in run, (
        "the build does not pass the resolved version, so each cell would "
        "resolve its own again")


def test_the_dockerfile_still_builds_without_the_arg():
    """The fallback is load-bearing: a local or manual `docker build` passes no
    build-arg, and must not break. Verified by real docker build on 2026-08-17
    (arg passed, arg absent, and arg with a leading 'v' all produce v2.1.3)."""
    df = (REPO / "Dockerfile").read_text()
    assert 'ARG SYNCTHING_VERSION=""' in df, (
        "the ARG must default to empty so an unset build-arg is legal")
    assert "${SYNCTHING_VERSION:-$(curl" in df, (
        "an empty ARG must fall back to resolving the version in-build")
    assert "${SYNCTHING_VERSION#v}" in df, (
        "the tag_name carries a leading 'v' that the download URL adds back")


# ---- redraw on any failure, and name the host (2026-08-19) -------------------

QA_GATE = REPO / ".github" / "workflows" / "qa-gate.yml"


def _qa_gate_run() -> str:
    d = yaml.safe_load(QA_GATE.read_text())
    steps = d["jobs"]["qa"]["steps"]
    return " ".join(str(s.get("run", "")) for s in steps)


def test_a_failing_cell_redraws_instead_of_blocking_on_one_host():
    """Reproducibility is the discriminator, not the symptom.

    The 2026-08-18 pytorch gate blocked six cells; every one investigated passed
    on other hardware — an NCCL segfault inside libcuda on one machine (two
    controls on the identical driver build passed), two collectives timeouts (the
    same test then passed 10/10 on the same GPU model, driver and image digest),
    and three supervisord boot races. A rule that only redrew when NO test failed
    could not help with any of them, because a bad host makes real tests fail.
    """
    run = _qa_gate_run()
    assert '[ "$CODE" -ne 0 ] && [ "$CODE" -ne 4 ]' in run, (
        "a failing cell must redraw on any non-zero exit except config_error; "
        "keying on 'no test failed' misses every host fault that breaks a test")
    assert 'CODE=2' in run, "the redraw path must mark the attempt inconclusive"


def test_config_error_is_never_retried():
    """config_error is OUR bug. Retrying it hides it."""
    run = _qa_gate_run()
    assert '"$CODE" -ne 4' in run, (
        "config_error (4) must be excluded from the redraw, or a broken gate "
        "retries itself into looking healthy")


def test_the_failing_machine_is_NAMED_before_the_instance_is_destroyed():
    """A de-verification candidate is only actionable if the machine id is
    captured at the moment it failed — the instance is destroyed immediately
    after, and the offer is gone."""
    run = _qa_gate_run()
    assert ".machine_id" in run, (
        "the gate never reads machine_id, so a bad host cannot be identified")
    assert "SUSPECT-HOST" in run, "the failing host is not announced in the log"
    assert "qa-suspect-hosts.txt" in run, (
        "suspects must accumulate across attempts, not just the last one")


def test_the_client_reports_the_machine_it_ran_on():
    """qa-gate can only name the host if test_template.py puts it in --raw."""
    src = (REPO / "tools" / "template_manager" / "test_template.py").read_text()
    assert 'raw_output["machine_id"] = machine_id' in src, (
        "the client does not surface machine_id, so the gate has nothing to read")
    assert 'return instance_id, test_url, auth_token, offer.get("machine_id")' in src, (
        "machine_id must travel out of the launch loop with the instance")


def test_exoneration_is_distinguished_from_a_still_suspect_image():
    """A machine list means opposite things depending on whether a redraw passed.

    Passed after redraw -> the image is exonerated, the host is at fault, de-verify.
    Never passed        -> the image is still a live suspect; those hosts are NOT
                           evidence and must not be de-verified on this basis.
    """
    d = yaml.safe_load(QA_GATE.read_text())
    step = [s for s in d["jobs"]["qa"]["steps"] if s.get("name") == "Report suspect hosts"]
    assert step, "no step reports the suspect hosts"
    body = str(step[0].get("run", ""))
    assert "De-verification candidates" in body and "NOT exonerated" in body, (
        "the report must distinguish a redraw-exonerated image from one that "
        "never passed; otherwise good hosts get de-verified for an image bug")
    assert 'FINAL_CODE' in body, "the distinction must key on the cell's final outcome"


# ---- the notifier must survive any caller's headline -------------------------

NOTIFY = REPO / ".github" / "workflows" / "notify-slack.yml"


def test_the_slack_header_is_clamped_to_slacks_limit():
    """Observed 2026-08-19, run 32239585814.

    promote-pytorch built a 527-character headline enumerating the QA'd
    artifacts. Slack's header block is plain_text with a hard 150-character
    limit, so the whole POST was rejected with HTTP 400 and the notification was
    lost — including the part that says whether anything shipped. The promotion
    had fully SUCCEEDED, every tag pushed, and the run still reported failure
    because the only thing that broke was the announcement of the success.

    This file already truncates the tags section for its 3000-char limit; the
    header was never clamped, so any of the five callers could break the shared
    notifier.
    """
    body = NOTIFY.read_text()
    assert "${#HEADER_TEXT} -gt 150" in body, (
        "the header is not clamped to Slack's 150-char limit, so a long headline "
        "from any caller loses the entire notification to an HTTP 400")


def test_the_clamped_text_is_not_simply_discarded():
    """A headline long enough to clamp is carrying information. The body has a
    3000-char limit rather than 150, so the overflow belongs there — silently
    dropping it would trade one lost message for a misleading one."""
    body = NOTIFY.read_text()
    assert "HEADER_OVERFLOW" in body, "the clamped text is discarded"
    assert "section" in body.split('if [[ -n "$HEADER_OVERFLOW" ]]')[-1][:400], (
        "the overflow must be re-attached as a body block, not thrown away")


def test_the_clamp_is_defensive_in_the_shared_notifier_not_only_the_caller():
    """Fixing only promote-pytorch would leave the same trap for the other four
    callers. The component that knows Slack's limits is the one that must hold
    them."""
    callers = [p.name for p in (REPO / ".github" / "workflows").glob("*.yml")
               if p.name != "notify-slack.yml" and "notify-slack.yml" in p.read_text()]
    assert len(callers) >= 3, (
        f"expected several callers of the shared notifier, found {callers}")
    assert "${#HEADER_TEXT} -gt 150" in NOTIFY.read_text(), (
        "the clamp must live in the shared notifier so no caller can break it")


def test_the_word_boundary_cut_does_not_eat_the_budget():
    """The trim must be conditional on the TRIMMED length, not the pre-trim one.

    The first version tested `${#_cut}`, which is always 147 at that point, so
    the condition was vacuously true. A headline whose tail is a space-free list
    — a comma-joined tag list — then trimmed back to the last space in the prose
    prefix. Measured: 194 characters in, 27 out. The full text survived in the
    overflow block, so nothing was lost, but the header collapsed to a fifth of
    its budget.
    """
    body = NOTIFY.read_text()
    assert "${#_trim} -gt 120" in body, (
        "the word-boundary guard must test the trimmed string; testing the "
        "fixed-length cut is vacuously true and throws away the budget")
    assert "${#_cut} -gt 120" not in body, (
        "this is the vacuous form — _cut is always 147 where it is tested")


# ---- the truth rule must reach EVERY caller, not only the two it was written for ----
#
# The rule above ("requiring the single way it goes right cannot be missed") was fixed
# in promote-base-image.yml, and later in build-llama-cpp.yml, one file at a time. It
# was never made a rule ABOUT ALL CALLERS, and build-llama-cpp.yml's own comment says so
# in as many words: "the same defect still sits in build-vllm.yml, build-sglang.yml and
# build-comfyui.yml, which share this expression verbatim... the shared guard is the
# real fix."
#
# It bit on 2026-08-27. Three branch dispatches were cancelled at the production
# approval gate — deliberately, because their images predated a fix. Every QA cell had
# passed, so `qa.outputs.gated` was 'true' and `merge-manifests` ended 'cancelled',
# which is not 'failure'. Slack announced:
#
#     :x: vLLM promoted — live-GPU QA passed
#     :x: SGLang promoted — live-GPU QA passed
#
# next to a red run card, for two images that were not promoted and could not have been.
# The icon was right (build-result reads merge-manifests.result) and the sentence was
# false, which is the worst of both: a reader who trusts the words is misinformed and a
# reader who trusts the icon learns to ignore the words.

WF = REPO / ".github" / "workflows"

# The job whose SUCCESS is what "promoted" means. Two names across the whole tree; the
# convention is load-bearing, so a workflow that promotes under a third name must either
# adopt one of these or extend this tuple deliberately.
_PROMOTING_JOBS = ("promote", "merge-manifests")


def _promotion_claiming_notifies():
    """Every notify job whose headline can RENDER a claim that something shipped."""
    out = []
    for f in sorted(WF.glob("*.yml")):
        try:
            data = yaml.safe_load(f.read_text())
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for jn, j in (data.get("jobs") or {}).items():
            if not isinstance(j, dict) or not str(j.get("uses", "")).endswith("notify-slack.yml"):
                continue
            headline = " ".join(str((j.get("with") or {}).get("headline", "")).split())
            # A fixed string that consults no job result is not derived from outcomes —
            # its own `if:` is the allowlist. qa-gate's soft-pass warning is that shape.
            if "needs." not in headline:
                continue
            # "NOT promoted" is the honest branch, not a claim.
            if not re.search(r"(?<!NOT )promoted", headline):
                continue
            needs = j.get("needs") or []
            if isinstance(needs, str):
                needs = [needs]
            promoter = next((p for p in _PROMOTING_JOBS if p in needs), None)
            out.append((f.name, jn, headline, promoter))
    return out


def test_the_walker_actually_finds_the_callers():
    """A guard that silently matches nothing is worse than none: it reports green
    forever. Pin the floor so a refactor that renames `notify` or the reusable
    workflow fails HERE rather than quietly switching the rule off."""
    found = _promotion_claiming_notifies()
    names = sorted({n for n, _, _, _ in found})
    assert len(names) >= 4, f"expected several promotion-claiming notifiers, found {names}"
    for expected in ("build-llama-cpp.yml", "promote-base-image.yml"):
        assert expected in names, f"{expected} should be in scope but was not matched: {names}"


def _promotion_problems(name, jn, headline, promoter) -> list[str]:
    """The rule itself, taking a headline rather than reading one — so a mutation test
    can feed it a corrupted version of the REAL expression."""
    if promoter is None:
        return [f"{name}:{jn} claims a promotion but needs no job named {_PROMOTING_JOBS}"]
    if f"needs.{promoter}.result != 'success'" not in headline:
        return [f"{name}:{jn} can render 'promoted' without requiring `{promoter}` to "
                f"SUCCEED — a cancelled or skipped {promoter} falls through to the claim"]
    return []


def test_every_promotion_claim_is_an_ALLOWLIST_on_the_promoting_job():
    """THE rule. A headline may say "promoted" only where the promoting job is
    required to have SUCCEEDED — never as the fall-through of a list of failure
    states, because the state nobody enumerated ('cancelled', 'skipped', and
    whatever GitHub adds next) lands on the success side."""
    bad = [p for args in _promotion_claiming_notifies() for p in _promotion_problems(*args)]
    assert not bad, "promotion claims that are not allowlisted:\n  " + "\n  ".join(bad)


def test_mut_removing_the_allowlist_from_a_real_headline_is_caught():
    """Mutation against the REAL expression rather than a synthetic one.

    Take build-vllm.yml's headline as it now ships, delete the clause added on
    2026-08-27, and the rule must fire again. Without this the rule could be softened
    to something vacuously true and every assertion above would stay green — which is
    how the original enumeration survived two fixes.
    """
    found = {n: (jn, h, pr) for n, jn, h, pr in _promotion_claiming_notifies()}
    assert "build-vllm.yml" in found, f"build-vllm.yml no longer in scope: {sorted(found)}"
    jn, headline, promoter = found["build-vllm.yml"]
    assert _promotion_problems("build-vllm.yml", jn, headline, promoter) == [], (
        "the shipped headline should already satisfy the rule")

    mutated = headline.replace(
        "|| (needs.merge-manifests.result != 'success' "
        "&& 'vLLM NOT promoted — promotion did not run')", "")
    assert mutated != headline, (
        "the mutation matched nothing — the allowlist clause has been reworded, so "
        "this test is no longer mutating anything")
    assert _promotion_problems("build-vllm.yml", jn, mutated, promoter), (
        "removing the allowlist clause did not trip the rule")


def test_the_not_promoted_branch_names_the_state_and_does_not_open_with_a_claim():
    """The negative branch has to READ as negative. "X promoted — but ..." at the
    start of a Slack line is what people see; burying the negation mid-sentence is
    how the 2026-08-14 "promoted — N tag(s) HELD" line misled everyone who saw it."""
    bad = []
    for name, jn, headline, promoter in _promotion_claiming_notifies():
        if promoter is None:
            continue
        m = re.search(rf"needs\.{re.escape(promoter)}\.result != 'success' && (?:format\()?'([^']+)'", headline)
        if not m:
            continue                      # allowlist absence is the previous test's report
        msg = m.group(1)
        if "NOT promoted" not in msg and "BLOCKED" not in msg:
            bad.append(f"{name}:{jn} not-promoted branch does not say so: {msg!r}")
    assert not bad, "\n  ".join(bad)


def test_the_guard_runs_when_a_BUILD_workflow_changes():
    """This file reads .github/workflows/**, but imagegen-tests.yml only triggers on
    tools/**. So the guard could not see the very edit that breaks it: a PR touching
    only build-vllm.yml's headline would never run this test."""
    ci = yaml.safe_load((WF / "imagegen-tests.yml").read_text())
    triggers = ci.get(True) or ci.get("on")
    for event in ("push", "pull_request"):
        paths = ((triggers or {}).get(event) or {}).get("paths") or []
        assert any(p.startswith(".github/workflows/") and p.rstrip("*").rstrip("/") == ".github/workflows"
                   for p in paths), (
            f"imagegen-tests.yml does not run on {event} to .github/workflows/**, so the "
            f"notification-truth guard cannot see a headline edit; paths={paths}")
