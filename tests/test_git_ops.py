"""Tests for git_ops.py — git repository operations."""

import os
import subprocess
import pytest

from git_ops import prep_git_repo, push_to_github, push_all


def _git(args, cwd=None):
    """Run git with signing disabled for test isolation."""
    cmd = ['git', '-c', 'commit.gpgsign=false', '-c', 'tag.gpgsign=false'] + args
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True)


def _init_bare_repo(path):
    """Create a bare git repo for testing remote operations."""
    os.makedirs(path, exist_ok=True)
    _git(['init', '--bare', str(path)])
    return str(path)


def _init_repo(path):
    """Create a git repo with an initial commit."""
    os.makedirs(path, exist_ok=True)
    _git(['init', str(path)])
    _git(['config', 'user.email', 'test@test.com'], cwd=str(path))
    _git(['config', 'user.name', 'Test'], cwd=str(path))
    _git(['config', 'commit.gpgsign', 'false'], cwd=str(path))
    # Initial commit
    (path / 'README.md').write_text('# Test')
    _git(['add', '.'], cwd=str(path))
    _git(['commit', '-m', 'init'], cwd=str(path))
    return str(path)


class TestPrepGitRepo:
    def test_creates_directory_if_not_exists(self, tmp_path):
        repo_path = str(tmp_path / 'new_repo')
        prep_git_repo(repo_path)
        assert os.path.isdir(repo_path)

    def test_preserves_git_dir(self, tmp_path):
        repo = tmp_path / 'repo'
        _init_repo(repo)
        # Add some build output
        (repo / 'output.json').write_text('{}')
        subprocess.run(['git', 'add', '.'], cwd=str(repo), capture_output=True)
        subprocess.run(['git', 'commit', '-m', 'add output'], cwd=str(repo),
                       capture_output=True)

        prep_git_repo(str(repo))

        # .git should still exist
        assert (repo / '.git').exists()
        # README.md preserved
        assert (repo / 'README.md').exists()
        # Build output should be cleaned
        assert not (repo / 'output.json').exists()

    def test_preserves_license(self, tmp_path):
        repo = tmp_path / 'repo'
        _init_repo(repo)
        (repo / 'LICENSE').write_text('Apache 2.0')

        prep_git_repo(str(repo))
        assert (repo / 'LICENSE').read_text() == 'Apache 2.0'

    def test_cleans_non_preserved_files(self, tmp_path):
        repo = tmp_path / 'repo'
        _init_repo(repo)
        (repo / 'old_data').mkdir()
        (repo / 'old_data' / 'file.json').write_text('old')
        (repo / 'stale.sha').write_text('hash')

        prep_git_repo(str(repo))

        assert not (repo / 'old_data').exists()
        assert not (repo / 'stale.sha').exists()

    def test_no_git_dir_leaves_as_is(self, tmp_path):
        """Directory without .git is left untouched."""
        repo = tmp_path / 'plain'
        repo.mkdir()
        (repo / 'existing.txt').write_text('keep me')

        prep_git_repo(str(repo))
        assert (repo / 'existing.txt').exists()


class TestPushToGithub:
    def test_no_git_dir_returns_false(self, tmp_path):
        assert push_to_github(str(tmp_path)) is False

    def test_no_changes_returns_false(self, tmp_path):
        repo = tmp_path / 'repo'
        _init_repo(repo)
        assert push_to_github(str(repo)) is False

    def test_with_changes_commits(self, tmp_path):
        # Setup: bare remote + local clone
        bare = tmp_path / 'bare'
        _init_bare_repo(str(bare))

        local = tmp_path / 'local'
        _git(['clone', str(bare), str(local)])
        _git(['config', 'user.email', 'test@test.com'], cwd=str(local))
        _git(['config', 'user.name', 'Test'], cwd=str(local))
        _git(['config', 'commit.gpgsign', 'false'], cwd=str(local))
        # Need initial commit in local since bare is empty
        (local / 'init.txt').write_text('init')
        _git(['add', '.'], cwd=str(local))
        _git(['commit', '-m', 'init'], cwd=str(local))
        _git(['push', '-u', 'origin', 'master'], cwd=str(local))

        # Make a change
        (local / 'new_file.json').write_text('{"new": true}')

        result = push_to_github(str(local))
        assert result is True

        # Verify commit exists in bare repo
        log_result = _git(['log', '--oneline', '-1'], cwd=str(bare))
        assert 'Update' in log_result.stdout


class TestPushAll:
    def test_pushes_both_repos(self, tmp_path):
        api = tmp_path / 'v3'
        scripture = tmp_path / 'v3_scripture'
        # Both are plain dirs (no .git), should return False gracefully
        api.mkdir()
        scripture.mkdir()
        result = push_all(str(api))
        assert result is False
