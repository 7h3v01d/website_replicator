"""core — framework-agnostic replication logic"""
from .models import ReplicatorConfig, AnalysisResult, QueueItem, DownloadResult
from .replicator import Replicator, analyse
from .downloader import Downloader
