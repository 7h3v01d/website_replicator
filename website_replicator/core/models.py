"""
core/models.py
Pure data classes shared between core logic and UI.
No Qt, no aiohttp — importable in tests without any GUI env.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------------------
# Settings (plain dataclass — AppSettings in ui/ wraps this with QSettings)
# ---------------------------------------------------------------------------

@dataclass
class ReplicatorConfig:
    """All tuneable parameters for a replication job."""
    max_retries:   int   = 5
    timeout:       int   = 20
    user_agent:    str   = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    )
    server_port:   int   = 8000
    crawl_depth:   int   = 0       # 0 = homepage only
    max_concurrent: int  = 6       # semaphore slot count
    output_dir:    str   = "replicated_website"
    passthru:      bool  = False
    resume:        bool  = True    # skip already-downloaded assets
    domain_delay:  float = 0.0     # seconds between requests to same domain
    blacklist:     tuple  = ()     # glob patterns — matching URLs are skipped


# ---------------------------------------------------------------------------
# Analysis result
# ---------------------------------------------------------------------------

@dataclass
class AnalysisResult:
    url:              str
    css_count:        int = 0
    js_count:         int = 0
    img_count:        int = 0
    webp_count:       int = 0
    picture_count:    int = 0
    misc_count:       int = 0
    inline_count:     int = 0
    internal_links:   set  = field(default_factory=set)
    external_domains: set  = field(default_factory=set)
    cors_restrictive: bool = False
    dynamic_indicators: int = 0
    feasibility:      str = "Unknown"   # "Complete" | "Partial" | "None"
    robots_disallowed: list = field(default_factory=list)  # disallowed paths
    reason:           str = ""
    # Raw soup kept for reuse during replication (not serialised)
    soup:             object = field(default=None, repr=False)

    @property
    def total_assets(self) -> int:
        return self.css_count + self.js_count + self.img_count + self.picture_count + self.misc_count

    def summary_lines(self) -> list[str]:
        ext = ", ".join(self.external_domains) if self.external_domains else "None"
        return [
            "HTML Files: 1",
            f"CSS Files: {self.css_count}",
            f"JavaScript Files: {self.js_count}",
            f"Images: {self.img_count} (including {self.webp_count} WebP)",
            f"Picture Elements: {self.picture_count}",
            f"Misc Assets: {self.misc_count}",
            f"Inline Scripts: {self.inline_count}",
            f"Internal Links Found: {len(self.internal_links)}",
            f"External Domains: {len(self.external_domains)} ({ext})",
            f"CORS Restrictive: {'Yes' if self.cors_restrictive else 'No'}",
            f"Replication Feasibility: {self.feasibility}",
            f"Reason: {self.reason}",
        ]


# ---------------------------------------------------------------------------
# Queue item
# ---------------------------------------------------------------------------

@dataclass
class QueueItem:
    PENDING   = "Pending"
    ACTIVE    = "Active"
    DONE      = "Done"
    ERROR     = "Error"
    CANCELLED = "Cancelled"

    url:         str
    passthru:    bool
    output_dir:  str
    crawl_depth: int
    status:      str      = field(default=PENDING)
    added_at:    str      = field(default_factory=lambda: datetime.now().strftime("%H:%M:%S"))
    pages_done:  int      = 0
    pages_total: int      = 0

    def label(self) -> str:
        depth_tag = f"  (depth {self.crawl_depth})" if self.crawl_depth > 0 else ""
        progress  = f"  {self.pages_done}/{self.pages_total}p" if self.pages_total > 0 else ""
        return f"[{self.added_at}] {self.url}{depth_tag}  —  {self.status}{progress}"


# ---------------------------------------------------------------------------
# Download result (used internally by Downloader)
# ---------------------------------------------------------------------------

@dataclass
class DownloadResult:
    url:      str
    filepath: Optional[str]   # None on failure
    cached:   bool = False    # True if returned from asset_map dedup
    error:    Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.filepath is not None
