"""Tests for git_ops.py — git repository operations."""

import os
import subprocess
import pytest

from git_ops import (
    GitOperationError,
    GitPushError,
    GitRepository,
    prep_git_repo,
    push_all,
    push_all_repos,
    push_to_github,
)
from publication_safety import PublicationSafetyError


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
    def test_no_git_dir_is_a_required_repository_failure(self, tmp_path):
        with pytest.raises(GitOperationError, match='missing .git directory'):
            push_to_github(str(tmp_path))

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
    def test_missing_required_repositories_fail(self, tmp_path):
        with pytest.raises(PublicationSafetyError, match='root does not exist'):
            push_all(str(tmp_path / 'v3'))

    def test_existing_plain_directory_is_not_treated_as_no_changes(self, tmp_path):
        api = tmp_path / 'v3'
        scripture = tmp_path / 'v3_scripture'
        api.mkdir()
        scripture.mkdir()

        with pytest.raises(GitOperationError, match='missing .git directory'):
            push_all(str(api))


class TestPublicationSafety:
    def test_growth_baseline_survives_output_cleanup(self, tmp_path):
        repo_path = tmp_path / 'repo'
        _init_repo(repo_path)
        generated = repo_path / 'kjv.json'
        generated.write_bytes(b'x' * 100)
        _git(['add', '.'], cwd=str(repo_path))
        _git(['commit', '-m', 'baseline'], cwd=str(repo_path))

        repo = GitRepository(str(repo_path), max_file_bytes=10_000)
        repo.prepare()
        assert not generated.exists()

        generated.write_bytes(b'x' * 126)
        with pytest.raises(PublicationSafetyError) as exc_info:
            repo.validate_output()

        message = str(exc_info.value)
        assert str(generated) in message
        assert 'previously 100 bytes' in message

    def test_explicit_growth_override_is_scoped_to_growth(self, tmp_path):
        repo_path = tmp_path / 'repo'
        _init_repo(repo_path)
        generated = repo_path / 'kjv.json'
        generated.write_bytes(b'x' * 10)
        _git(['add', '.'], cwd=str(repo_path))
        _git(['commit', '-m', 'baseline'], cwd=str(repo_path))

        repo = GitRepository(
            str(repo_path), allow_output_growth=True, max_file_bytes=100,
        )
        repo.prepare()
        generated.write_bytes(b'x' * 99)
        repo.validate_output()

        generated.write_bytes(b'x' * 100)
        with pytest.raises(PublicationSafetyError, match='hard ceiling'):
            repo.validate_output()


class TestPushFailures:
    def test_gh001_is_permanent_and_not_retried(self, tmp_path, monkeypatch):
        repo = GitRepository(str(tmp_path))
        calls = []

        def failed_push(args, cwd=None, timeout=None):
            calls.append(args)
            return (
                1,
                '',
                'remote: error: File kjv.json is 556 MB; this exceeds GitHub\'s '
                'file size limit of 100 MB\nremote: error: GH001: Large files detected.',
            )

        monkeypatch.setattr(repo, '_run', failed_push)
        monkeypatch.setattr('git_ops.time.sleep', lambda _: pytest.fail('must not retry'))

        with pytest.raises(GitPushError) as exc_info:
            repo._push_with_retry()

        assert exc_info.value.permanent is True
        assert exc_info.value.attempts == 1
        assert calls == [['push']]

    def test_remote_rejection_is_permanent(self, tmp_path, monkeypatch):
        repo = GitRepository(str(tmp_path))
        calls = []

        def rejected(args, cwd=None, timeout=None):
            calls.append(args)
            return 1, '', '! [remote rejected] main -> main (pre-receive hook declined)'

        monkeypatch.setattr(repo, '_run', rejected)

        with pytest.raises(GitPushError) as exc_info:
            repo._push_with_retry()

        assert exc_info.value.permanent is True
        assert len(calls) == 1

    def test_transient_push_retries_then_succeeds(self, tmp_path, monkeypatch):
        repo = GitRepository(str(tmp_path))
        outcomes = iter([
            (1, '', 'connection timed out'),
            (1, '', 'temporary failure resolving host'),
            (0, '', ''),
        ])
        sleeps = []
        monkeypatch.setattr(repo, '_run', lambda *args, **kwargs: next(outcomes))
        monkeypatch.setattr('git_ops.time.sleep', sleeps.append)

        assert repo._push_with_retry() is True
        assert sleeps == [2, 4]

    def test_transient_push_exhaustion_raises(self, tmp_path, monkeypatch):
        repo = GitRepository(str(tmp_path))
        calls = []

        def unavailable(*args, **kwargs):
            calls.append(1)
            return 1, '', 'connection timed out'

        monkeypatch.setattr(repo, '_run', unavailable)
        monkeypatch.setattr('git_ops.time.sleep', lambda _: None)

        with pytest.raises(GitPushError) as exc_info:
            repo._push_with_retry()

        assert exc_info.value.permanent is False
        assert exc_info.value.attempts == 4
        assert len(calls) == 4

    @pytest.mark.parametrize('failed_operation', ['status', 'add', 'commit'])
    def test_required_local_git_failure_raises(
        self, tmp_path, monkeypatch, failed_operation,
    ):
        repo_path = tmp_path / 'repo'
        _init_repo(repo_path)
        (repo_path / 'changed.json').write_text('{}')
        repo = GitRepository(str(repo_path))
        original_run = repo._run

        def fail_selected(args, cwd=None, timeout=300):
            if args[0] == failed_operation:
                return 1, '', f'{failed_operation} failed deliberately'
            return original_run(args, cwd=cwd, timeout=timeout)

        monkeypatch.setattr(repo, '_run', fail_selected)

        with pytest.raises(GitOperationError) as exc_info:
            repo.push()

        assert exc_info.value.operation == failed_operation


class _RecordingRepo:
    def __init__(self, result=False, error=None):
        self.result = result
        self.error = error
        self.calls = 0
        self.validation_calls = 0

    def validate_output(self):
        self.validation_calls += 1

    def push(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class TestFailClosedPushOrder:
    def test_scripture_failure_prevents_hash_attempt(self):
        error = GitPushError('/scripture', 'GH001', attempts=1, permanent=True)
        scripture = _RecordingRepo(error=error)
        hashes = _RecordingRepo(result=True)

        with pytest.raises(GitPushError):
            push_all_repos(scripture, hashes)

        assert scripture.calls == 1
        assert hashes.calls == 0

    def test_no_changes_is_not_a_failure_and_hash_is_attempted(self):
        scripture = _RecordingRepo(result=False)
        hashes = _RecordingRepo(result=False)

        assert push_all_repos(scripture, hashes) is False
        assert scripture.calls == 1
        assert hashes.calls == 1

    def test_hash_failure_propagates_after_scripture_success(self):
        scripture = _RecordingRepo(result=True)
        error = GitOperationError('commit', '/hash', 'failed')
        hashes = _RecordingRepo(error=error)

        with pytest.raises(GitOperationError):
            push_all_repos(scripture, hashes)

        assert scripture.calls == 1
        assert hashes.calls == 1

    def test_hash_preflight_failure_prevents_any_commit_or_push(self):
        scripture = _RecordingRepo(result=True)
        hashes = _RecordingRepo(result=True)

        def fail_validation():
            hashes.validation_calls += 1
            raise PublicationSafetyError('hash output too large')

        hashes.validate_output = fail_validation

        with pytest.raises(PublicationSafetyError, match='hash output too large'):
            push_all_repos(scripture, hashes)

        assert scripture.validation_calls == 1
        assert hashes.validation_calls == 1
        assert scripture.calls == 0
        assert hashes.calls == 0
