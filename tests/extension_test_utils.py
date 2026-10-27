"""Shared helpers for local extension fixtures."""

from pathlib import Path


def write_catalog_epoch(workspace: Path, epoch: int) -> None:
    pending = workspace / "catalog_epoch.pending"
    pending.write_text(str(epoch), encoding="utf-8")
    pending.replace(workspace / "catalog_epoch")
