"""Fail-closed safety gates for generated publication repositories."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from typing import Iterable, Mapping


MAX_PUBLISHED_FILE_BYTES = 95 * 1024 * 1024
DEFAULT_MAX_GROWTH_RATIO = 0.25


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
    baseline_json_sizes: Mapping[str, int] | None = None,
    allow_growth: bool = False,
    max_file_bytes: int = MAX_PUBLISHED_FILE_BYTES,
    max_growth_ratio: float = DEFAULT_MAX_GROWTH_RATIO,
    preserved_names: Iterable[str] = (),
):
    """Reject oversized files and unexpected growth of tracked generated JSON.

    The absolute file ceiling is never bypassed. ``allow_growth`` only disables
    comparison with the previous committed JSON blobs and is intended for an
    explicitly reviewed schema expansion.
    """

    if max_file_bytes <= 0:
        raise ValueError("max_file_bytes must be positive")
    if max_growth_ratio < 0:
        raise ValueError("max_growth_ratio must not be negative")

    files = list(iter_generated_files(root, preserved_names))
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

    if allow_growth or not baseline_json_sizes:
        return files

    growth = []
    multiplier = 1.0 + max_growth_ratio
    for item in files:
        if not item.relative_path.endswith(".json"):
            continue
        baseline_size = baseline_json_sizes.get(item.relative_path)
        if baseline_size is None:
            continue
        if baseline_size == 0:
            if item.size > 0:
                growth.append((item, baseline_size, float("inf")))
            continue
        ratio = item.size / baseline_size
        if ratio > multiplier:
            growth.append((item, baseline_size, ratio))

    if growth:
        details = "\n".join(
            _format_growth(item, baseline_size, ratio)
            for item, baseline_size, ratio in growth
        )
        raise PublicationSafetyError(
            "tracked generated JSON exceeded the allowed growth of "
            f"{max_growth_ratio:.0%}; review the schema change and rerun with the "
            "explicit output-growth override if intentional:\n"
            f"{details}"
        )

    return files


def _format_growth(item: GeneratedFile, baseline_size: int, ratio: float) -> str:
    if ratio == float("inf"):
        change = "new content from a 0-byte baseline"
    else:
        change = f"{ratio - 1.0:.2%} growth"
    return (
        f"- {item.absolute_path}: {item.size:,} bytes, previously "
        f"{baseline_size:,} bytes ({change})"
    )


def _mib(size: int) -> float:
    return size / (1024 * 1024)


def _raise_walk_error(error: OSError) -> None:
    raise PublicationSafetyError(
        f"could not inspect generated publication output: {error}"
    ) from error
