"""Common shapes both jar providers (Mojang, PaperMC) return. Neither
dataclass carries provider-specific knowledge -- the orchestrator in
services/jars.py only ever deals with these two types."""
from dataclasses import dataclass


@dataclass
class VersionInfo:
    version: str
    label: str


@dataclass
class DownloadInfo:
    url: str
    filename: str
    expected_hash: str
    hash_algorithm: str
