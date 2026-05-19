"""
tests/test_models.py
Unit tests for core/models.py
"""

import pytest
from website_replicator.core.models import (
    AnalysisResult, QueueItem, DownloadResult, ReplicatorConfig
)


class TestAnalysisResult:
    def test_total_assets_sum(self):
        r = AnalysisResult(
            url="https://x.com",
            css_count=2, js_count=3, img_count=10,
            picture_count=1, misc_count=2,
        )
        assert r.total_assets == 18

    def test_summary_lines_count(self):
        r = AnalysisResult(url="https://x.com", feasibility="Complete", reason="OK")
        lines = r.summary_lines()
        assert len(lines) == 12

    def test_summary_contains_feasibility(self):
        r = AnalysisResult(url="https://x.com", feasibility="Partial", reason="Dynamic")
        text = "\n".join(r.summary_lines())
        assert "Partial" in text

    def test_external_domains_in_summary(self):
        r = AnalysisResult(
            url="https://x.com",
            external_domains={"cdn.net", "fonts.googleapis.com"},
        )
        text = "\n".join(r.summary_lines())
        assert "cdn.net" in text or "fonts.googleapis.com" in text

    def test_cors_yes_no(self):
        r_cors = AnalysisResult(url="https://x.com", cors_restrictive=True)
        r_open = AnalysisResult(url="https://x.com", cors_restrictive=False)
        assert "Yes" in "\n".join(r_cors.summary_lines())
        assert "No"  in "\n".join(r_open.summary_lines())


class TestQueueItem:
    def test_default_status_pending(self):
        item = QueueItem(url="https://x.com", passthru=False,
                         output_dir="/tmp/out", crawl_depth=0)
        assert item.status == QueueItem.PENDING

    def test_label_includes_url(self):
        item = QueueItem(url="https://x.com", passthru=False,
                         output_dir="/tmp/out", crawl_depth=0)
        assert "https://x.com" in item.label()

    def test_label_includes_status(self):
        item = QueueItem(url="https://x.com", passthru=False,
                         output_dir="/tmp/out", crawl_depth=0)
        item.status = QueueItem.ACTIVE
        assert QueueItem.ACTIVE in item.label()

    def test_label_includes_depth_when_nonzero(self):
        item = QueueItem(url="https://x.com", passthru=False,
                         output_dir="/tmp/out", crawl_depth=2)
        assert "depth 2" in item.label()

    def test_label_no_depth_when_zero(self):
        item = QueueItem(url="https://x.com", passthru=False,
                         output_dir="/tmp/out", crawl_depth=0)
        assert "depth" not in item.label()

    def test_status_constants_distinct(self):
        statuses = {
            QueueItem.PENDING, QueueItem.ACTIVE, QueueItem.DONE,
            QueueItem.ERROR, QueueItem.CANCELLED,
        }
        assert len(statuses) == 5


class TestDownloadResult:
    def test_ok_true_when_filepath_set(self):
        r = DownloadResult(url="https://x.com/a.css", filepath="/tmp/a.css")
        assert r.ok

    def test_ok_false_when_filepath_none(self):
        r = DownloadResult(url="https://x.com/a.css", filepath=None)
        assert not r.ok

    def test_cached_defaults_false(self):
        r = DownloadResult(url="https://x.com/a.css", filepath="/tmp/a.css")
        assert not r.cached

    def test_error_field(self):
        r = DownloadResult(url="https://x.com/a.css", filepath=None, error="HTTP 404")
        assert r.error == "HTTP 404"


class TestReplicatorConfig:
    def test_defaults(self):
        cfg = ReplicatorConfig()
        assert cfg.max_retries == 5
        assert cfg.crawl_depth == 0
        assert cfg.max_concurrent == 6
        assert cfg.timeout == 20
        assert not cfg.passthru

    def test_custom_values(self):
        cfg = ReplicatorConfig(crawl_depth=3, max_concurrent=10, passthru=True)
        assert cfg.crawl_depth == 3
        assert cfg.max_concurrent == 10
        assert cfg.passthru
