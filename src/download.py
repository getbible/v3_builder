"""
Crosswire SWORD module downloader for getBible API.

Downloads Bible modules from the Crosswire SWORD project repositories.
Tries the RAW format first, falls back to WIN format and converts to RAW.
"""

import argparse
import json
import logging
import os
import shutil
import sys
import urllib.request
import zipfile

log = logging.getLogger(__name__)

# Crosswire mirror base URLs
_RAW_URL = 'https://www.crosswire.org/ftpmirror/pub/sword/packages/rawzip'
_WIN_URL = 'https://www.crosswire.org/ftpmirror/pub/sword/packages/win'


def download_modules(module_names, output_path):
    """Download SWORD modules from Crosswire repositories.

    For each module, attempts to download the RAW zip format first.
    If the RAW format is invalid, falls back to the WIN format and
    converts it to RAW.

    Args:
        module_names: Dict mapping SWORD module names to abbreviations
                      (only keys are used).
        output_path: Directory to save downloaded .zip files.

    Returns:
        List of paths to successfully downloaded .zip files.
    """
    os.makedirs(output_path, exist_ok=True)

    total = len(module_names)
    downloaded = []

    for i, sword_name in enumerate(module_names, 1):
        file_path = os.path.join(output_path, f'{sword_name}.zip')
        log.info('[%d/%d] Processing %s', i, total, sword_name)

        if os.path.isfile(file_path) and _valid_zip(file_path):
            log.info('[%d/%d] %s.zip already exists', i, total, sword_name)
            downloaded.append(file_path)
            continue
        if os.path.exists(file_path):
            os.remove(file_path)
            log.warning('[%d/%d] Removed invalid cached %s.zip', i, total, sword_name)

        # Try RAW format first
        if _download_raw(sword_name, file_path):
            downloaded.append(file_path)
            continue

        # Fall back to WIN format and convert
        if _download_and_convert_win(sword_name, file_path, output_path):
            downloaded.append(file_path)
            continue

        log.warning('[%d/%d] Failed to download %s from any source', i, total, sword_name)

    log.info('Downloaded %d/%d modules to %s', len(downloaded), total, output_path)
    return downloaded


def _download_raw(sword_name, file_path):
    """Download a module in RAW zip format.

    Args:
        sword_name: SWORD module name.
        file_path: Local path to save the zip file.

    Returns:
        True if downloaded and valid, False otherwise.
    """
    url = f'{_RAW_URL}/{sword_name}.zip'
    temporary = file_path + '.part'
    if os.path.exists(temporary):
        os.remove(temporary)

    try:
        log.info('Downloading RAW format: %s', sword_name)
        urllib.request.urlretrieve(url, temporary)
    except (urllib.error.URLError, OSError) as e:
        log.warning('RAW download failed for %s: %s', sword_name, e)
        if os.path.exists(temporary):
            os.remove(temporary)
        return False

    if _valid_zip(temporary):
        os.replace(temporary, file_path)
        return True

    # Invalid zip — clean up
    if os.path.exists(temporary):
        os.remove(temporary)
        log.warning('%s.zip (RAW) was invalid and removed', sword_name)
    return False


def _download_and_convert_win(sword_name, file_path, output_path):
    """Download WIN format and convert to RAW zip.

    The WIN format contains a data.zip with a 'newmods' directory that
    must be renamed to 'mods.d' to create a valid RAW format.

    Args:
        sword_name: SWORD module name.
        file_path: Local path to save the final zip file.
        output_path: Working directory for temporary extraction.

    Returns:
        True if converted successfully, False otherwise.
    """
    url = f'{_WIN_URL}/{sword_name}.zip'
    win_zip_path = os.path.join(output_path, f'{sword_name}_win.zip')
    raw_zip_path = file_path + '.part'
    for temporary in (win_zip_path, raw_zip_path):
        if os.path.exists(temporary):
            os.remove(temporary)

    try:
        log.info('Downloading WIN format: %s', sword_name)
        urllib.request.urlretrieve(url, win_zip_path)
    except (urllib.error.URLError, OSError) as e:
        log.warning('WIN download failed for %s: %s', sword_name, e)
        return False

    if not _valid_zip(win_zip_path):
        if os.path.exists(win_zip_path):
            os.remove(win_zip_path)
            log.warning('%s.zip (WIN) was invalid and removed', sword_name)
        return False

    # Convert WIN to RAW format
    folder_path = os.path.join(output_path, sword_name)
    raw_path = os.path.join(folder_path, 'RAW')

    try:
        log.info('Converting %s from WIN to RAW format', sword_name)

        # Extract WIN zip
        with zipfile.ZipFile(win_zip_path, 'r') as zf:
            zf.extractall(folder_path)
        os.remove(win_zip_path)

        # Extract the inner data.zip to RAW directory
        data_zip = os.path.join(folder_path, 'data.zip')
        with zipfile.ZipFile(data_zip, 'r') as zf:
            zf.extractall(raw_path)
        os.remove(data_zip)

        # Rename newmods → mods.d
        newmods = os.path.join(raw_path, 'newmods')
        modsd = os.path.join(raw_path, 'mods.d')
        if os.path.isdir(newmods):
            os.rename(newmods, modsd)

        # Re-zip as RAW format
        _create_zip(raw_path, raw_zip_path)
        if not _valid_zip(raw_zip_path):
            raise zipfile.BadZipFile('converted RAW archive failed validation')
        os.replace(raw_zip_path, file_path)
        log.info('Converted %s to RAW format', sword_name)
        return True

    except (zipfile.BadZipFile, OSError, KeyError) as e:
        log.error('Conversion failed for %s: %s', sword_name, e)
        return False

    finally:
        # Clean up temporary extraction directory
        if os.path.isdir(folder_path):
            shutil.rmtree(folder_path, ignore_errors=True)
        if os.path.exists(win_zip_path):
            os.remove(win_zip_path)
        if os.path.exists(raw_zip_path):
            os.remove(raw_zip_path)


def _valid_zip(path):
    """Return whether ``path`` is a complete, readable ZIP archive."""

    try:
        with zipfile.ZipFile(path, 'r') as archive:
            return archive.testzip() is None
    except (FileNotFoundError, OSError, zipfile.BadZipFile):
        return False


def _create_zip(source_dir, zip_path):
    """Create a zip file from a directory without changing working directory.

    Args:
        source_dir: Directory to zip.
        zip_path: Output zip file path.
    """
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(source_dir):
            for name in files:
                full_path = os.path.join(root, name)
                arcname = os.path.relpath(full_path, source_dir)
                zf.write(full_path, arcname)


def parse_args(argv=None):
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description='Download Crosswire SWORD Bible modules',
    )
    parser.add_argument('--output_path', required=True,
                        help='Directory to save downloaded .zip files')
    parser.add_argument('--bible_conf', required=True,
                        help='Path to Bible modules map JSON')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Enable debug logging')
    return parser.parse_args(argv)


def main(argv=None):
    """Main entry point."""
    args = parse_args(argv)

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%H:%M:%S',
    )

    with open(args.bible_conf, 'r') as f:
        module_names = json.load(f)

    downloaded = download_modules(module_names, args.output_path)
    log.info('Complete: %d modules downloaded', len(downloaded))
    return 0


if __name__ == '__main__':
    sys.exit(main())
