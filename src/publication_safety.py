"""Fail-closed safety gates for generated publication repositories."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from typing import Iterable


MAX_PUBLISHED_FILE_BYTES = 95 * 1024 * 1024


class PublicationSafetyError(RuntimeError):
    """Raised when generated output is unsafe to publish."""


@dataclass(frozen=True)
class GeneratedFile:
    """A regular generated file and its exact on-disk size."""

    relative_path: str
    absolute_path: str
    size: int


def iter_generated_files(root: str, preserved_names: Iterable[str] = ()):
    """Yield regular output files, excluding Git and preserved repository metadata.

    Symlinks and other special filesystem entries are intentionally not followed.
    Paths are returned in deterministic repository-relative order.
    """

    root = os.path.abspath(root)
    try:
        root_mode = os.lstat(root).st_mode
    except FileNotFoundError:
        raise PublicationSafetyError(
            f"generated publication root does not exist: {root}"
        ) from None
    if not stat.S_ISDIR(root_mode):
        raise PublicationSafetyError(
            f"generated publication root is not a real directory: {root}"
        )
    preserved = frozenset(preserved_names)
    files = []
    for directory, dirnames, filenames in os.walk(
        root,
        followlinks=False,
        onerror=_raise_walk_error,
    ):
        relative_directory = os.path.relpath(directory, root)
        traversable_directories = []
        for dirname in sorted(dirnames):
            if relative_directory == "." and dirname in preserved:
                continue
            absolute_directory = os.path.join(directory, dirname)
            mode = os.lstat(absolute_directory).st_mode
            if not stat.S_ISDIR(mode):
                relative_path = os.path.relpath(absolute_directory, root).replace(
                    os.sep, "/"
                )
                raise PublicationSafetyError(
                    "generated publication output contains a symlink or special "
                    f"filesystem entry: {absolute_directory} ({relative_path})"
                )
            traversable_directories.append(dirname)
        if relative_directory == ".":
            dirnames[:] = [
                name for name in traversable_directories
                if name != ".git" and name not in preserved
            ]
        else:
            dirnames[:] = traversable_directories

        for filename in sorted(filenames):
            if relative_directory == "." and filename in preserved:
                continue
            absolute_path = os.path.join(directory, filename)
            try:
                mode = os.lstat(absolute_path).st_mode
            except FileNotFoundError:
                raise PublicationSafetyError(
                    f"generated output changed while it was being inspected: {absolute_path}"
                ) from None
            if not stat.S_ISREG(mode):
                relative_path = os.path.relpath(absolute_path, root).replace(os.sep, "/")
                raise PublicationSafetyError(
                    "generated publication output contains a symlink or special "
                    f"filesystem entry: {absolute_path} ({relative_path})"
                )
            relative_path = os.path.relpath(absolute_path, root).replace(os.sep, "/")
            try:
                size = os.path.getsize(absolute_path)
            except FileNotFoundError:
                raise PublicationSafetyError(
                    f"generated output changed while it was being inspected: {absolute_path}"
                ) from None
            files.append(
                GeneratedFile(
                    relative_path=relative_path,
                    absolute_path=absolute_path,
                    size=size,
                )
            )
    yield from sorted(files, key=lambda item: item.relative_path)


def validate_generated_output(
    root: str,
    *,
    max_file_bytes: int = MAX_PUBLISHED_FILE_BYTES,
    preserved_names: Iterable[str] = (),
):
    """Reject filesystem hazards and files GitHub cannot accept.

    Content size may legitimately change whenever CrossWire updates a module or
    Builder learns to project more of it.  Only the absolute per-file ceiling is
    enforced; the builder does not try to infer upstream correctness from a
    comparison with the previous publication.
    """

    if max_file_bytes <= 0:
        raise ValueError("max_file_bytes must be positive")

    files = list(iter_generated_files(root, preserved_names))
    incomplete_writes = [
        item
        for item in files
        if os.path.basename(item.relative_path).startswith('.')
        and item.relative_path.endswith('.tmp')
    ]
    if incomplete_writes:
        details = "\n".join(
            f"- {item.absolute_path}: {item.size:,} bytes"
            for item in incomplete_writes
        )
        raise PublicationSafetyError(
            "generated publication output contains incomplete atomic JSON "
            f"writes; refuse to hash or publish:\n{details}"
        )
    oversized = [item for item in files if item.size >= max_file_bytes]
    if oversized:
        details = "\n".join(
            f"- {item.absolute_path}: {item.size:,} bytes ({_mib(item.size):.2f} MiB)"
            for item in oversized
        )
        raise PublicationSafetyError(
            "generated publication files reached the hard ceiling "
            f"of {max_file_bytes:,} bytes ({_mib(max_file_bytes):.2f} MiB):\n{details}"
        )

    return files


def _mib(size: int) -> float:
    return size / (1024 * 1024)


def _raise_walk_error(error: OSError) -> None:
    raise PublicationSafetyError(
        f"could not inspect generated publication output: {error}"
    ) from error
