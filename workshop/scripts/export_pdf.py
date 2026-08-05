#!/usr/bin/env python3
"""Export Vega Concierge Hugo workshop to a single print-quality PDF.

Uses Safari.app File → Export as PDF on each page (same as manual export), then
merges. Requires macOS + Safari + Accessibility permission for the terminal app.
"""

from __future__ import annotations

import argparse
import platform
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

PAGE_HAS_CONTENT_JS = """
() => {
  const article = document.querySelector('article.content');
  if (!article) return false;
  const clone = article.cloneNode(true);
  clone.querySelectorAll(
    'nav.pager, nav.breadcrumb, .toc, .page-meta-footer'
  ).forEach((el) => el.remove());
  const text = (clone.textContent || '').replace(/\\s+/g, ' ').trim();
  if (text.length < 40) return false;
  if (/headless\\s*=\\s*true/i.test(text)) return false;
  if (/publishResources\\s*=\\s*true/i.test(text) && text.length < 120) return false;
  return true;
}
"""

# AppleScript: load URL in Safari, then File → Export as PDF (menu name varies by locale).
EXPORT_PAGE_APPLESCRIPT = r"""
on run argv
    set pageURL to item 1 of argv
    set outPath to item 2 of argv

    tell application "Safari"
        activate
        if (count of windows) = 0 then make new document
        set w to window 1
        set t to current tab of w
        set URL of t to pageURL

        set maxWait to 60
        set n to 0
        repeat while n < maxWait
            try
                if (do JavaScript "document.readyState" in t) is "complete" then exit repeat
            end try
            delay 0.5
            set n to n + 0.5
        end repeat

        delay 2
    end tell

    tell application "System Events"
        tell process "Safari"
            set frontmost to true
            click menu bar item "File" of menu bar 1
            delay 0.25
            set fileMenu to menu "File" of menu bar 1
            set clickedExport to false
            repeat with mi in menu items of fileMenu
                try
                    set nm to name of mi as text
                    if nm contains "PDF" then
                        click mi
                        set clickedExport to true
                        exit repeat
                    end if
                end try
            end repeat
            if clickedExport is false then error "Safari File menu has no Export as PDF item"
            delay 1
            keystroke "G" using {command down, shift down}
            delay 0.4
            keystroke outPath
            keystroke return
            delay 0.6
            keystroke return
            delay 0.4
            try
                keystroke return
            end try
        end tell
    end tell
end run
"""

SAFARI_SETUP_HELP = """
PDF export drives Safari.app (File → Export as PDF) via AppleScript.

One-time setup:
  1. Safari installed (default on macOS)
  2. System Settings → Privacy & Security → Accessibility
     Enable your terminal app (Terminal, iTerm, or Cursor)
  3. Keep Safari as the frontmost app while export runs (do not switch away)

Then re-run:
  ./workshop/scripts/export-pdf.sh
""".strip()


def require_safari() -> None:
    if platform.system() != "Darwin":
        raise SystemExit(
            "PDF export requires macOS with Safari.\n"
            "Only Safari → Export as PDF renders this workshop correctly."
        )
    if not Path("/Applications/Safari.app").exists():
        raise SystemExit("Safari.app not found in /Applications.")


def export_page_via_safari(url: str, output: Path) -> None:
    """Safari File → Export as PDF for one URL."""
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    result = subprocess.run(
        ["osascript", "-", url, str(output.resolve())],
        input=EXPORT_PAGE_APPLESCRIPT,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        if "assistive access" in err.lower() or "not allowed" in err.lower():
            raise SystemExit(SAFARI_SETUP_HELP)
        raise SystemExit(f"Safari export failed for {url}:\n{err}")

    if not output.exists() or output.stat().st_size < 500:
        raise SystemExit(
            f"Safari did not write {output}.\n"
            f"{SAFARI_SETUP_HELP}"
        )


def collect_pager_urls(page, base_url: str, start_path: str, *, max_pages: int) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    path = start_path if start_path.startswith("/") else f"/{start_path}"

    for _ in range(max_pages):
        if path in seen:
            break
        seen.add(path)
        paths.append(path)
        page.goto(urljoin(base_url, path), wait_until="load", timeout=120_000)
        next_loc = page.locator("nav.pager a.pager__btn--next")
        if next_loc.count() == 0:
            break
        href = next_loc.first.get_attribute("href")
        if not href:
            break
        path = urlparse(href).path

    return paths


def md_path_to_url(md_path: Path, vega_root: Path) -> str:
    rel = md_path.relative_to(vega_root)
    if rel.name == "_index.md":
        parts = rel.parent.parts
        if not parts:
            return "/workshops/vega/"
        return "/workshops/vega/" + "/".join(parts) + "/"
    return f"/workshops/vega/{rel.with_suffix('')}/".replace("\\", "/")


def collect_skip_paths(workshop_dir: Path) -> set[str]:
    skip: set[str] = {"/workshops/vega/images/"}
    vega = workshop_dir / "content" / "workshops" / "vega"
    if not vega.is_dir():
        return skip

    for md in vega.rglob("*.md"):
        text = md.read_text(encoding="utf-8")
        if re.search(r"^headless\s*=\s*true", text, re.MULTILINE):
            skip.add(md_path_to_url(md, vega))
        if re.search(r"render\s*=\s*false", text) and md.parent.name == "images":
            skip.add(md_path_to_url(md, vega))

    return skip


def collect_appendix_urls(workshop_dir: Path) -> list[str]:
    appendix = workshop_dir / "content" / "workshops" / "vega" / "appendix"
    if not appendix.is_dir():
        return []

    pages: list[tuple[int, str]] = []
    for md in appendix.glob("*.md"):
        if md.name == "_index.md":
            rel = "/workshops/vega/appendix/"
            weight = 0
        else:
            rel = f"/workshops/vega/appendix/{md.stem}/"
            weight = 999
        text = md.read_text(encoding="utf-8")
        m = re.search(r"^weight\s*=\s*(\d+)", text, re.MULTILINE)
        if m:
            weight = int(m.group(1))
        pages.append((weight, rel))

    pages.sort(key=lambda t: (t[0], t[1]))
    return [p for _, p in pages]


def filter_export_paths(page, base_url: str, paths: list[str], skip: set[str]) -> list[str]:
    kept: list[str] = []
    for rel in paths:
        if rel in skip:
            print(f"  skip (bundle): {rel}")
            continue
        page.goto(urljoin(base_url, rel), wait_until="load", timeout=120_000)
        if page.evaluate(PAGE_HAS_CONTENT_JS):
            kept.append(rel)
        else:
            print(f"  skip (empty): {rel}")
    return kept


def merge_pdfs(parts: list[Path], output: Path) -> None:
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as exc:
        raise SystemExit(
            "PDF merge needs pypdf. Run: pip install -r workshop/requirements-export.txt"
        ) from exc

    writer = PdfWriter()
    for part in parts:
        reader = PdfReader(str(part))
        writer.append_pages_from_reader(reader)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as fh:
        writer.write(fh)


def export_safari_pages(
    base_url: str,
    paths: list[str],
    output: Path,
) -> None:
    print("==> Safari File → Export as PDF (one page at a time)")
    print("    Keep Safari in front; grant Accessibility to this terminal if prompted.")

    parts: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="vega-export-") as tmp:
        tmp_dir = Path(tmp)
        for i, rel in enumerate(paths, 1):
            url = urljoin(base_url, rel)
            part = tmp_dir / f"page-{i:03d}.pdf"
            print(f"  export [{i:02d}/{len(paths):02d}] {rel}")
            export_page_via_safari(url, part)
            parts.append(part)
        merge_pdfs(parts, output)


def add_pdf_metadata(output: Path, *, title: str, author: str) -> None:
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        return

    reader = PdfReader(str(output))
    writer = PdfWriter()
    writer.append_pages_from_reader(reader)
    writer.add_metadata(
        {
            "/Title": title,
            "/Author": author,
            "/Subject": "Vega Concierge hands-on workshop guide",
            "/Creator": "vega-concierge workshop/scripts/export-pdf.sh",
        }
    )
    with output.open("wb") as fh:
        writer.write(fh)


def export_pdf(
    *,
    base_url: str,
    start_path: str,
    output: Path,
    include_appendix: bool,
    workshop_dir: Path,
    title: str,
    author: str,
) -> None:
    require_safari()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(
            "Playwright not installed (used to discover page order). Run:\n"
            "  pip install -r workshop/requirements-export.txt\n"
            "  playwright install chromium"
        ) from exc

    skip = collect_skip_paths(workshop_dir)
    print("==> engine: Safari.app Export as PDF")

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(color_scheme="light")
        try:
            paths = collect_pager_urls(page, base_url, start_path, max_pages=200)
            if include_appendix:
                for p in collect_appendix_urls(workshop_dir):
                    if p not in paths:
                        paths.append(p)
            paths = filter_export_paths(page, base_url, paths, skip)
        finally:
            browser.close()

    if not paths:
        raise SystemExit(f"No exportable workshop pages at {base_url}{start_path}")

    print(f"==> exporting {len(paths)} pages")
    export_safari_pages(base_url, paths, output)

    add_pdf_metadata(output, title=title, author=author)

    size_kb = output.stat().st_size // 1024
    print(f"Done: {output} ({size_kb} KB, {len(paths)} pages, Safari Export as PDF)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Hugo workshop to PDF")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--start-path", default="/workshops/vega/")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workshop-dir", type=Path, required=True)
    parser.add_argument("--public-dir", type=Path, required=True)
    parser.add_argument("--include-appendix", action="store_true")
    parser.add_argument("--title", default="Vega Concierge · Workshop")
    parser.add_argument("--author", default="Fernando Dumont")
    args = parser.parse_args()

    if not args.base_url.endswith("/"):
        args.base_url += "/"

    export_pdf(
        base_url=args.base_url,
        start_path=args.start_path,
        output=args.output,
        include_appendix=args.include_appendix,
        workshop_dir=args.workshop_dir,
        title=args.title,
        author=args.author,
    )


if __name__ == "__main__":
    main()
