"""
File operations for getBible API builder.

Handles:
- Cleaning empty/invalid files from scripture output
- Copying public hash files from scripture repo to public API repo
- Minified JSON serialization for the public API output

Replaces movePublicHashFiles.sh and the cleanSystem() function from run.sh.
"""

import json
import logging
import os
import shutil

log = logging.getLogger(__name__)

# File patterns that should be copied to the public hash repository
PUBLIC_FILE_PATTERNS = (
    '.sha',
    'checksum',
    'checksum.json',
    'translations',
    'translations.json',
    'books',
    'books.json',
    'chapters',
    'chapters.json',
)


def write_json_minified(data, output_file):
    """Write ``data`` to ``output_file`` as minified JSON.

    The v3 API output is built for download speed, not human reading:
    every byte of indentation we ship is bandwidth a client pays for.
    Compact separators with ``ensure_ascii=False`` produce the smallest
    valid JSON. A trailing newline keeps files POSIX-clean (negligible
    cost; some tools assume it).

    Anyone who wants to inspect a file by hand can pipe it through
    ``python -m json.tool`` or ``jq`` — costs nothing and keeps the
    served files small.

    Args:
        data: JSON-serializable object to write.
        output_file: Destination path.
    """
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
        f.write('\n')


def clean_empty_files(scripture_path, min_size=500):
    """
    Remove empty/invalid JSON files and empty directories.

    Args:
        scripture_path: Path to the scripture output directory
        min_size: Minimum file size in bytes (files smaller are removed)

    Returns:
        Tuple of (files_removed, dirs_removed)
    """
    if not os.path.isdir(scripture_path):
        raise FileNotFoundError(f'Folder {scripture_path} not found')

    files_removed = 0
    dirs_removed = 0

    # Remove small JSON files (likely empty/invalid)
    for dirpath, dirnames, filenames in os.walk(scripture_path):
        for filename in filenames:
            if filename.endswith('.json'):
                filepath = os.path.join(dirpath, filename)
                if os.path.getsize(filepath) < min_size:
                    os.remove(filepath)
                    files_removed += 1
                    log.debug('Removed small file: %s', filepath)

    # Remove empty directories (bottom-up)
    for dirpath, dirnames, filenames in os.walk(scripture_path, topdown=False):
        if dirpath == scripture_path:
            continue
        if not os.listdir(dirpath):
            os.rmdir(dirpath)
            dirs_removed += 1
            log.debug('Removed empty dir: %s', dirpath)

    log.info('Cleaned %d files and %d directories', files_removed, dirs_removed)
    return files_removed, dirs_removed


def _is_public_file(filename):
    """Check if a filename matches public hash file patterns."""
    if filename.endswith('.sha'):
        return True
    return filename in PUBLIC_FILE_PATTERNS


def move_public_hash_files(scripture_path, hash_path):
    """
    Copy public hash/checksum files from scripture repo to public API repo.

    Preserves directory structure. Creates directories as needed.
    Cleans old files from hash_path first (preserving .git, LICENSE, README.md).

    Args:
        scripture_path: Source path (scripture repository with hash files)
        hash_path: Destination path (public API repository)

    Returns:
        Number of files copied
    """
    if not os.path.isdir(scripture_path):
        raise FileNotFoundError(f'Scripture folder {scripture_path} not found')

    # Clean old files from hash_path, preserving git metadata
    _clean_hash_dir(hash_path)

    files_copied = 0

    for dirpath, dirnames, filenames in os.walk(scripture_path):
        for filename in filenames:
            if not _is_public_file(filename):
                continue

            src = os.path.join(dirpath, filename)
            # Compute relative path from scripture_path
            rel = os.path.relpath(src, scripture_path)
            dst = os.path.join(hash_path, rel)

            # Ensure destination directory exists
            dst_dir = os.path.dirname(dst)
            os.makedirs(dst_dir, exist_ok=True)

            shutil.copy2(src, dst)
            files_copied += 1
            log.debug('Copied %s', rel)

    log.info('Copied %d public hash files to %s', files_copied, hash_path)
    return files_copied


def _clean_hash_dir(hash_path):
    """
    Remove old files from hash directory, preserving .git, LICENSE, README.md.
    """
    if not os.path.isdir(hash_path):
        os.makedirs(hash_path, exist_ok=True)
        return

    preserve = {'.git', '.github', '.gitignore', 'LICENSE', 'README.md'}

    for entry in os.listdir(hash_path):
        if entry in preserve:
            continue
        full = os.path.join(hash_path, entry)
        if os.path.isdir(full):
            shutil.rmtree(full)
        else:
            os.remove(full)

    log.debug('Cleaned hash directory %s', hash_path)
