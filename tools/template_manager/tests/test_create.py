"""create.py: inject_readme, the --image/--tag override, and single-file mode."""
import re

import create
from create import inject_readme, process_template_dir, process_single_file
from models import VastTemplate


class _RecordingManager:
    """Minimal stand-in: records the templates handed to create_or_preview.

    In dry-run, process_* only calls create_or_preview (the referral/get_user_id
    path is skipped), so this is all that's needed to assert on the result.
    """
    def __init__(self):
        self.created = []

    def create_or_preview(self, template, dry_run=False):
        self.created.append(template)
        return None


def _readme(tmp_path, text):
    p = tmp_path / "README.md"
    p.write_text(text)
    return p


def test_image_tag_override_reflected_in_payload():
    # The CI override mutates a pydantic extra="forbid" model; assignment must
    # work and to_api_dict must reflect the new image/tag.
    t = VastTemplate(name="t", image="orig", tag="t1")
    t.image, t.tag = "staging-ns/comfy", "ref-cuda-12.9-py312-amd64"
    d = t.to_api_dict()
    assert d["image"] == "staging-ns/comfy"
    assert d["tag"] == "ref-cuda-12.9-py312-amd64"


def test_override_applied_through_process_template_dir(tmp_path):
    d = tmp_path / "tpl"
    d.mkdir()
    (d / "template.yml").write_text("name: T\nimage: orig\ntag: t1\n")
    mgr = _RecordingManager()
    process_template_dir(d, mgr, dry_run=True,
                         image_override="newimg", tag_override="newtag")
    assert mgr.created[0].image == "newimg"
    assert mgr.created[0].tag == "newtag"


def test_single_file_mode_does_not_inject_sibling_readme(tmp_path):
    (tmp_path / "my.yml").write_text("name: T\nimage: i\n")
    (tmp_path / "README.md").write_text("sibling readme that must NOT be used")
    mgr = _RecordingManager()
    results = process_single_file(tmp_path / "my.yml", mgr, dry_run=True)
    assert mgr.created[0].readme is None        # __no_readme__ sentinel honored
    assert "dir" not in results[0]              # single-file mode strips the dir key


def test_missing_readme_leaves_template_unchanged(tmp_path):
    t = VastTemplate(name="t")
    out = inject_readme(t, tmp_path / "nope.md", "t")
    assert out.readme is None


def test_dry_run_uses_placeholder(tmp_path):
    p = _readme(tmp_path, "launch: <<LAUNCH_LINK>>")
    out = inject_readme(VastTemplate(name="t"), p, "t", dry_run=True)
    assert "[LAUNCH_LINK_PLACEHOLDER]" in out.readme
    assert "<<LAUNCH_LINK>>" not in out.readme


def test_live_substitutes_referral_url(tmp_path):
    p = _readme(tmp_path, "launch: <<LAUNCH_LINK>>")
    out = inject_readme(VastTemplate(name="t"), p, "t", referral_url="https://x/y")
    assert "https://x/y" in out.readme


def test_template_name_substituted(tmp_path):
    p = _readme(tmp_path, "Welcome to <<TEMPLATE_NAME>>")
    out = inject_readme(VastTemplate(name="t"), p, "My Image")
    assert "Welcome to My Image" in out.readme


def test_appends_single_updated_stamp(tmp_path):
    p = _readme(tmp_path, "body")
    out = inject_readme(VastTemplate(name="t"), p, "t")
    assert len(re.findall(r"updated \d{4}-\d{2}-\d{2} \d{2}:\d{2}", out.readme)) == 1


def test_existing_stamp_is_replaced_not_duplicated(tmp_path):
    p = _readme(tmp_path, "body\nupdated 2020-01-01 00:00")
    out = inject_readme(VastTemplate(name="t"), p, "t")
    assert len(re.findall(r"updated \d{4}-\d{2}-\d{2} \d{2}:\d{2}", out.readme)) == 1
    assert "2020-01-01 00:00" not in out.readme


def test_stamp_only_readme_does_not_accumulate(tmp_path):
    # A README that is *only* an updated-line must not gain a second stamp.
    p = _readme(tmp_path, "updated 2020-01-01 00:00")
    out = inject_readme(VastTemplate(name="t"), p, "t")
    assert len(re.findall(r"updated \d{4}-\d{2}-\d{2} \d{2}:\d{2}", out.readme)) == 1
