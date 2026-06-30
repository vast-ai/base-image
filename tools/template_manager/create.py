#!/usr/bin/env python3
"""
Create Vast.ai templates from local YAML.

Entry point accepts either a single YAML file or a directory of template
subdirectories. Each subdirectory should contain template.yml and optionally
README.md. Always creates new templates via POST.

Usage:
    python create.py path/to/templates/ --api-key KEY     # dir of <name>/{template.yml,README.md}
    python create.py path/to/templates/ --dry-run
    python create.py my-template.yml --readme my-readme.md --api-key KEY
"""
import argparse
import json
import os
import re
import sys
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

# Resolve directories before any local imports
_GENERATOR_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_GENERATOR_ROOT))

from dotenv import load_dotenv
load_dotenv(_GENERATOR_ROOT / ".env")

from models import VastTemplate
from template_manager import TemplateManager


# ---------------------------------------------------------------------------
# Helpers for the create-from-directory flow
# ---------------------------------------------------------------------------

def discover_template_dirs(templates_dir: Path) -> List[Path]:
    """
    Discover template subdirectories containing template.yml.

    Returns sorted list of directories.
    """
    dirs = []
    for child in sorted(templates_dir.iterdir()):
        if child.is_dir() and (child / "template.yml").exists():
            dirs.append(child)
    return dirs


def inject_readme(
    template: VastTemplate,
    readme_path: Path,
    template_name: str,
    referral_url: Optional[str] = None,
    dry_run: bool = False,
) -> VastTemplate:
    """Read README.md and inject into the template readme field.

    Supports placeholders:
        <<LAUNCH_LINK>>         - Vast.ai launch URL with referral
        <<SELF_REFERRAL_URL>>   - Backward-compat alias for LAUNCH_LINK
        <<TEMPLATE_NAME>>       - The template name

    Appends an 'updated YYYY-MM-DD HH:MM' timestamp to the readme.
    """
    if not readme_path.exists():
        return template

    content = readme_path.read_text()

    # Substitute placeholders
    if dry_run:
        placeholder = "[LAUNCH_LINK_PLACEHOLDER]"
        content = content.replace("<<LAUNCH_LINK>>", placeholder)
        content = content.replace("<<SELF_REFERRAL_URL>>", placeholder)
    else:
        url = referral_url or ""
        content = content.replace("<<LAUNCH_LINK>>", url)
        content = content.replace("<<SELF_REFERRAL_URL>>", url)

    content = content.replace("<<TEMPLATE_NAME>>", template_name)

    # Strip any existing "updated ..." line and append fresh timestamp
    content = re.sub(r'(?:^|\n)updated \d{4}-\d{2}-\d{2} \d{2}:\d{2}\s*$', '', content)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    body = content.rstrip()
    # Avoid a leading blank line when the body is empty (e.g. a README that was
    # only a prior "updated ..." stamp).
    content = f"{body}\nupdated {now}" if body else f"updated {now}"

    template.readme = content
    return template


# ---------------------------------------------------------------------------
# Create-from-YAML entry point helpers
# ---------------------------------------------------------------------------

def load_template_from_yaml(yaml_path: Path) -> List[VastTemplate]:
    """Load and validate one or more templates from a YAML file."""
    try:
        return TemplateManager.load_templates_from_yaml(yaml_path)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def process_template_dir(
    template_dir: Path,
    manager: TemplateManager,
    dry_run: bool = False,
    readme_override: Optional[Path] = None,
    yaml_filename: str = "template.yml",
    image_override: Optional[str] = None,
    tag_override: Optional[str] = None,
) -> List[dict]:
    """
    Process a single template directory (template.yml + optional README.md).

    Always creates new templates via POST (see create_or_preview for rationale).

    Args:
        yaml_filename: Name of the YAML file inside template_dir (default
            ``template.yml``). Pass a different name for single-file mode.
        image_override / tag_override: replace the template's ``image``/``tag``
            before POST — used by CI to point a QA template at a freshly-built
            staging tag.

    Returns list of result dicts for logging.
    """
    yaml_path = template_dir / yaml_filename
    readme_path = readme_override or (template_dir / "README.md")

    templates = load_template_from_yaml(yaml_path)
    for t in templates:
        if image_override is not None:
            t.image = image_override
        if tag_override is not None:
            t.tag = tag_override
    results = []

    # Build referral URL once per directory
    referral_url = None
    if not dry_run and readme_path.exists():
        uid = manager.get_user_id()
        referral_url = TemplateManager.build_referral_url(uid, templates[0].name)

    for template in templates:
        template = inject_readme(
            template, readme_path, template.name,
            referral_url=referral_url, dry_run=dry_run
        )

        result = manager.create_or_preview(template, dry_run=dry_run)

        if result:
            results.append({
                "dir": template_dir.name,
                "name": result.get("name", template.name),
                "hash_id": result.get("hash_id", "N/A"),
                "id": result.get("id", "N/A"),
                "action": result.get("_action", "created"),
            })
        else:
            results.append({
                "dir": template_dir.name,
                "name": template.name,
                "action": "dry_run",
            })

    return results


def process_single_file(
    yaml_path: Path,
    manager: TemplateManager,
    dry_run: bool = False,
    readme_override: Optional[Path] = None,
    image_override: Optional[str] = None,
    tag_override: Optional[str] = None,
) -> List[dict]:
    """Process a standalone YAML file (backward-compatible mode).

    Delegates to process_template_dir using the file's parent directory.
    When no readme_override is provided, passes a non-existent sentinel
    path so that the sibling README.md is not auto-discovered.
    """
    # Use a sentinel readme path that won't exist so process_template_dir
    # skips auto-discovery of sibling README.md files.
    effective_readme = readme_override or (yaml_path.parent / "__no_readme__")

    results = process_template_dir(
        yaml_path.parent, manager, dry_run,
        readme_override=effective_readme, yaml_filename=yaml_path.name,
        image_override=image_override, tag_override=tag_override,
    )

    # Strip the "dir" key that process_template_dir adds
    for r in results:
        r.pop("dir", None)

    return results


def write_results_log(results: List[Dict], log_dir: Path, prefix: str):
    """Write results to a timestamped JSON log file."""
    if not results:
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"{timestamp}_{prefix}.json"
    log_data = {
        "timestamp": datetime.now().isoformat(),
        "templates": results,
    }
    with open(log_path, "w") as f:
        json.dump(log_data, f, indent=2)
    print(f"\nLog written to: {log_path}")


def print_results_summary(results: List[Dict]):
    """Print hash IDs and a create/dry-run summary."""
    if not results:
        return

    print("\n[Hash IDs]")
    for r in results:
        _print_hash_line(r)

    created = sum(1 for r in results if r["action"] == "created")
    previewed = sum(1 for r in results if r["action"] == "dry_run")
    parts = []
    if created:
        parts.append(f"{created} created")
    if previewed:
        parts.append(f"{previewed} previewed (dry run)")
    if parts:
        print(f"\n[Summary] {', '.join(parts)}")


def _print_hash_line(r: Dict):
    status = f"({r['action']})" if r["action"] != "dry_run" else "(dry run)"
    hid = r.get("hash_id", "")
    print(f"  {r['name']}: {hid} {status}")


def run(path: Path, api_key: str, dry_run: bool, readme: Optional[Path] = None,
        image_override: Optional[str] = None, tag_override: Optional[str] = None):
    """Main entry point."""
    over = {"image_override": image_override, "tag_override": tag_override}
    with TemplateManager(api_key=api_key) as manager:
        all_results = []

        if path.is_file():
            # Single file mode (backward compatible)
            print(f"Processing single file: {path}")
            results = process_single_file(path, manager, dry_run, readme_override=readme, **over)
            all_results.extend(results)

        elif path.is_dir():
            # Check if this directory itself has template.yml (single template dir)
            if (path / "template.yml").exists():
                print(f"Processing template directory: {path.name}")
                results = process_template_dir(path, manager, dry_run, readme_override=readme, **over)
                all_results.extend(results)
            else:
                # Directory iteration mode -- look for subdirectories with template.yml
                template_dirs = discover_template_dirs(path)
                if not template_dirs:
                    print(f"Error: No template directories found in {path}", file=sys.stderr)
                    print("Hint: Each subdirectory should contain template.yml", file=sys.stderr)
                    sys.exit(1)

                print(f"Discovered {len(template_dirs)} template director{'y' if len(template_dirs) == 1 else 'ies'}")
                for td in template_dirs:
                    print(f"  - {td.name}")

                for template_dir in template_dirs:
                    print(f"\nProcessing: {template_dir.name}")
                    results = process_template_dir(template_dir, manager, dry_run, readme_override=readme, **over)
                    all_results.extend(results)

        if not dry_run:
            write_results_log(all_results, _GENERATOR_ROOT / "output" / "logs", "create")
        print_results_summary(all_results)
        return all_results


def main():
    parser = argparse.ArgumentParser(
        description="Create Vast.ai templates from local YAML",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process all template directories
  python create.py recommended_templates/templates/ --api-key KEY

  # Dry run
  python create.py recommended_templates/templates/ --dry-run

  # Single YAML file with a readme
  python create.py my-template.yml --readme my-readme.md --api-key KEY

  # Single YAML file (backward compatible)
  python create.py my-template.yml --api-key KEY
        """,
    )
    parser.add_argument(
        "path",
        type=Path,
        nargs="?",
        help="Path to a YAML file, a single template directory, or a directory of template subdirectories",
    )
    parser.add_argument(
        "--api-key",
        help="Vast.ai API key (or set VAST_API_KEY in .env)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print payloads without creating templates",
    )
    parser.add_argument(
        "--readme",
        type=Path,
        help="Path to a .md or .txt file to inject as the template readme",
    )
    parser.add_argument(
        "--image",
        help="Override the template's image (e.g. point a QA template at a staging build)",
    )
    parser.add_argument(
        "--tag",
        help="Override the template's tag (e.g. the freshly-built staging tag)",
    )
    parser.add_argument(
        "--delete",
        type=int,
        metavar="TEMPLATE_ID",
        help="Delete a template by numeric id and exit (teardown of a throwaway QA template)",
    )
    parser.add_argument(
        "--emit-result",
        type=Path,
        metavar="PATH",
        help="Write the created templates (name/hash_id/id) as JSON to PATH (for CI to capture)",
    )
    args = parser.parse_args()

    api_key = args.api_key or os.getenv("VAST_API_KEY")

    # Teardown mode: delete a template by id and exit.
    if args.delete is not None:
        if not api_key:
            print("Error: API key required for --delete.", file=sys.stderr)
            sys.exit(1)
        with TemplateManager(api_key=api_key) as manager:
            manager.delete_template(args.delete)
        return

    if args.path is None:
        print("Error: a path is required (or use --delete TEMPLATE_ID).", file=sys.stderr)
        sys.exit(1)

    if not api_key and not args.dry_run:
        print("Error: API key required. Provide via --api-key or VAST_API_KEY env var.", file=sys.stderr)
        sys.exit(1)

    if not args.path.exists():
        print(f"Error: Path not found: {args.path}", file=sys.stderr)
        sys.exit(1)

    if args.readme:
        if not args.readme.exists():
            print(f"Error: Readme file not found: {args.readme}", file=sys.stderr)
            sys.exit(1)
        if args.readme.suffix.lower() not in (".md", ".txt"):
            print(f"Error: --readme must be a .md or .txt file, got: {args.readme.suffix}", file=sys.stderr)
            sys.exit(1)

    try:
        results = run(args.path, api_key or "", args.dry_run, readme=args.readme,
                      image_override=args.image, tag_override=args.tag)
    except urllib.error.HTTPError as e:
        # The API-layer handler already printed the status + body; exit cleanly
        # (no traceback) so CI logs show the actionable message, not a stack.
        print(f"Template API request failed: HTTP {e.code}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Template API connection failed: {e.reason}", file=sys.stderr)
        sys.exit(1)

    if args.emit_result:
        args.emit_result.write_text(json.dumps(results or [], indent=2))
        print(f"Wrote result to {args.emit_result}")


if __name__ == "__main__":
    main()
