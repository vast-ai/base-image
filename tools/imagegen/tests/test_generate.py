"""Round-trip: the generator's output must pass the linter, for every class.

This closes the loop — the trustworthy oracle (the linter, with its mutation
tests) validates the generator. Run via pytest or the stdlib fallback below.
"""
from pathlib import Path

from imagegen.generate import generate, CLASSES
from imagegen.discover import discover
from imagegen.linter import lint_image, ERROR


def _gen_repo(repo: Path):
    for d in ("external", "derivatives/pytorch/derivatives", "derivatives", ".github/workflows"):
        (repo / d).mkdir(parents=True, exist_ok=True)
    generate(repo, name="mytool", cls="derivative", label="My Tool", port=7860)
    generate(repo, name="myapp", cls="pytorch-nested", label="My App", port=7861)
    generate(repo, name="myext", cls="external", label="My Ext", port=7862,
             upstream="someupstream/img:1.0")


def test_generated_images_lint_clean(tmp_path):
    _gen_repo(tmp_path)
    imgs = {i.name: i for i in discover(tmp_path)}
    assert set(imgs) == {"mytool", "myapp", "myext"}
    for name, img in imgs.items():
        errors = [(f.code, f.msg) for f in lint_image(img, tmp_path) if f.severity == ERROR]
        assert not errors, f"{img.cls}/{name} should lint clean, got: {errors}"


def test_generated_classes_are_correct(tmp_path):
    _gen_repo(tmp_path)
    imgs = {i.name: i for i in discover(tmp_path)}
    assert imgs["mytool"].cls == "derivative"
    assert imgs["myapp"].cls == "pytorch-nested"
    assert imgs["myext"].cls == "external"


def test_external_requires_upstream(tmp_path):
    (tmp_path / "external").mkdir(parents=True, exist_ok=True)
    try:
        generate(tmp_path, name="x", cls="external", label="X", port=8000)
    except ValueError:
        return
    raise AssertionError("external without upstream should raise ValueError")


def test_fill_markers_present(tmp_path):
    """The judgment residue must be clearly fenced for the human/LLM to complete."""
    _gen_repo(tmp_path)
    for name, cls in (("mytool", "derivatives"), ("myapp", "derivatives/pytorch/derivatives"),
                      ("myext", "external")):
        df = (tmp_path / cls / name / "Dockerfile").read_text()
        assert ">>> FILL:" in df, f"{name} Dockerfile missing a FILL marker"


if __name__ == "__main__":
    import inspect, tempfile, traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            if inspect.signature(fn).parameters:
                with tempfile.TemporaryDirectory() as d:
                    fn(Path(d))
            else:
                fn()
            print("PASS", fn.__name__)
        except Exception:
            failed += 1
            print("FAIL", fn.__name__)
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
