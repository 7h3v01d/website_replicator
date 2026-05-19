"""
core/exporter.py
Post-replication export utilities.

  zip_output_dir(output_dir, zip_path)
      Compress the entire replicated site into a single ZIP archive.

  BrokenLinkReport
      Collected during replication; written to disk and returned as text.
"""

from __future__ import annotations

import os
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------------------
# ZIP export
# ---------------------------------------------------------------------------

def zip_output_dir(output_dir: str, zip_path: Optional[str] = None) -> str:
    """
    Compress everything inside *output_dir* into a ZIP archive.

    If *zip_path* is not given, the archive is placed next to *output_dir*
    with a timestamp suffix.

    Returns the path to the created ZIP file.
    """
    if zip_path is None:
        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        parent   = os.path.dirname(os.path.abspath(output_dir))
        basename = os.path.basename(output_dir.rstrip("/\\"))
        zip_path = os.path.join(parent, f"{basename}_{ts}.zip")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(output_dir):
            for filename in files:
                abs_path = os.path.join(root, filename)
                # Archive path relative to output_dir's parent so the zip
                # contains e.g.  replicated_website/index.html  not just index.html
                arc_path = os.path.relpath(abs_path, os.path.dirname(output_dir))
                zf.write(abs_path, arc_path)

    return zip_path


# ---------------------------------------------------------------------------
# Broken link report
# ---------------------------------------------------------------------------

@dataclass
class BrokenLink:
    """One failed asset download."""
    url:        str
    page_url:   str                     # which page referenced it
    asset_type: str                     # css / js / img / font / misc
    error:      str                     # HTTP status or exception message
    timestamp:  str = field(
        default_factory=lambda: datetime.now().strftime("%H:%M:%S")
    )


class BrokenLinkReport:
    """
    Accumulates broken links during a replication job and
    can write a structured text report to disk.

    Designed to be created once per Replicator job and passed
    into _process_page via the Downloader log callback chain.
    """

    def __init__(self, url: str, output_dir: str) -> None:
        self.site_url   = url
        self.output_dir = output_dir
        self._links:    list[BrokenLink] = []

    # ── public API ────────────────────────────────────────────────────────────

    def record(
        self,
        url:        str,
        page_url:   str,
        asset_type: str,
        error:      str,
    ) -> None:
        """Add one broken link to the report."""
        self._links.append(BrokenLink(
            url        = url,
            page_url   = page_url,
            asset_type = asset_type,
            error      = error,
        ))

    @property
    def count(self) -> int:
        return len(self._links)

    @property
    def has_errors(self) -> bool:
        return bool(self._links)

    def by_type(self) -> dict[str, list[BrokenLink]]:
        """Group broken links by asset_type."""
        groups: dict[str, list[BrokenLink]] = {}
        for link in self._links:
            groups.setdefault(link.asset_type, []).append(link)
        return groups

    def summary_lines(self) -> list[str]:
        """Short multi-line summary suitable for logging."""
        if not self._links:
            return ["No broken links detected ✓"]
        lines = [f"⚠ {self.count} broken link(s) detected:"]
        for atype, items in self.by_type().items():
            lines.append(f"  {atype}: {len(items)}")
        return lines

    def write(self, path: Optional[str] = None) -> str:
        """
        Write the full report to *path* (defaults to output_dir/broken_links.txt).
        Returns the path written.
        """
        if path is None:
            path = os.path.join(self.output_dir, "broken_links.txt")

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"Broken Link Report\n")
            fh.write(f"Site:      {self.site_url}\n")
            fh.write(f"Generated: {ts}\n")
            fh.write(f"Total:     {self.count} broken link(s)\n")
            fh.write("=" * 60 + "\n\n")

            if not self._links:
                fh.write("No broken links detected.\n")
            else:
                for atype, items in self.by_type().items():
                    fh.write(f"── {atype.upper()} ({len(items)}) ──\n")
                    for item in items:
                        fh.write(f"  [{item.timestamp}] {item.error}\n")
                        fh.write(f"    URL:  {item.url}\n")
                        fh.write(f"    Page: {item.page_url}\n\n")

        return path

    def as_text(self) -> str:
        """Return the full report as a string (for display in the UI)."""
        lines = [
            f"Broken Link Report — {self.site_url}",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Total: {self.count} broken link(s)",
            "=" * 60,
        ]
        if not self._links:
            lines.append("No broken links detected ✓")
        else:
            for atype, items in self.by_type().items():
                lines.append(f"\n── {atype.upper()} ({len(items)}) ──")
                for item in items:
                    lines.append(f"  [{item.timestamp}] {item.error}")
                    lines.append(f"    URL:  {item.url}")
                    lines.append(f"    Page: {item.page_url}")
        return "\n".join(lines)
