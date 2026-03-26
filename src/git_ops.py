"""
Git operations for getBible API builder.

Provides GitRepository for managing scripture/hash git repositories
(clone, pull, clean, commit, push) and a keepalive mechanism.
"""

import datetime
import logging
import os
import shutil
import subprocess
import time

log = logging.getLogger(__name__)

# Default timeout for most git commands (seconds).
_DEFAULT_TIMEOUT = 300

# Timeout for `git push` — large scripture/hash repos can contain
# 100K+ files and pushing deltas over the network can take many
# minutes, far exceeding the default timeout.
_PUSH_TIMEOUT = 1800

# Retry configuration for transient push failures (e.g. network
# errors, remote unavailable). Backoff doubles between attempts:
# 2s, 4s, 8s, 16s.
_PUSH_MAX_ATTEMPTS = 4
_PUSH_INITIAL_BACKOFF = 2


class GitRepository:
    """Manages a local git repository for the build pipeline.

    Encapsulates all git operations for a single repository: preparing
    (clone/pull/clean), committing, pushing, and keepalive updates.

    Args:
        path: Local filesystem path for the repository.
        repo_url: Remote URL for cloning (optional).
    """

    # Files preserved during repository reset
    _PRESERVE = frozenset({'.git', '.github', '.gitignore', 'LICENSE', 'README.md'})

    def __init__(self, path, repo_url=None):
        self._path = path
        self._repo_url = repo_url

    @property
    def path(self):
        """Local filesystem path of the repository."""
        return self._path

    @property
    def exists(self):
        """Whether the repository directory exists."""
        return os.path.isdir(self._path)

    @property
    def has_git(self):
        """Whether the repository has a .git directory."""
        return os.path.isdir(os.path.join(self._path, '.git'))

    def prepare(self, pull=False, preserve_extras=None):
        """Prepare the repository for a fresh build.

        If the repo doesn't exist:
        - Clone from remote URL if pull=True
        - Otherwise create the directory

        If the repo exists with .git:
        - Pull if pull=True
        - Preserve .git, LICENSE, README.md, .github, .gitignore
        - Remove everything else (clean slate for build output)

        Args:
            pull: Whether to clone/pull from remote.
            preserve_extras: Additional filenames to preserve.
        """
        preserve = set(self._PRESERVE)
        if preserve_extras:
            preserve.update(preserve_extras)

        if not self.exists:
            if pull and self._repo_url:
                log.info('Cloning %s into %s', self._repo_url, self._path)
                self._run(['clone', '--depth', '1', self._repo_url, self._path])
            else:
                os.makedirs(self._path, exist_ok=True)
                log.info('Created directory %s', self._path)
            return

        if self.has_git:
            if pull:
                log.info('Pulling latest changes in %s', self._path)
                self._run(['pull'], cwd=self._path)

            tmp_path = self._path + '_tmp'
            os.makedirs(tmp_path, exist_ok=True)

            for name in preserve:
                src = os.path.join(self._path, name)
                if os.path.exists(src):
                    dst = os.path.join(tmp_path, name)
                    if os.path.isdir(src):
                        shutil.copytree(src, dst, symlinks=True)
                    else:
                        shutil.copy2(src, dst)

            shutil.rmtree(self._path)
            os.rename(tmp_path, self._path)
            log.info('Reset %s (preserved git metadata)', self._path)
        else:
            log.info('Directory %s exists without .git, using as-is', self._path)

    def push(self, commit_message='Update'):
        """Stage all changes, commit, and push to remote.

        Args:
            commit_message: Commit message to use.

        Returns:
            True if changes were pushed, False if nothing to commit.
        """
        if not self.has_git:
            log.warning('No .git directory in %s, skipping push', self._path)
            return False

        rc, stdout, _ = self._run(['status', '--porcelain'], cwd=self._path)
        if rc != 0 or not stdout:
            log.info('Nothing to commit in %s', self._path)
            return False

        self._run(['add', '.'], cwd=self._path)
        rc, _, stderr = self._run(['commit', '-am', commit_message], cwd=self._path)
        if rc != 0:
            log.error('Commit failed in %s: %s', self._path, stderr)
            return False

        if not self._push_with_retry():
            return False

        log.info('Pushed changes from %s', self._path)
        return True

    def _push_with_retry(self):
        """Run `git push` with a large-repo timeout and retry on failure.

        Pushing the scripture/hash repositories can involve 100K+ files
        and take many minutes. A single attempt may time out or hit a
        transient network error, so we retry with exponential backoff.

        Returns:
            True on success, False if all attempts failed.
        """
        backoff = _PUSH_INITIAL_BACKOFF
        for attempt in range(1, _PUSH_MAX_ATTEMPTS + 1):
            rc, _, stderr = self._run(
                ['push'], cwd=self._path, timeout=_PUSH_TIMEOUT,
            )
            if rc == 0:
                return True
            log.warning(
                'Push attempt %d/%d failed in %s: %s',
                attempt, _PUSH_MAX_ATTEMPTS, self._path, stderr,
            )
            if attempt < _PUSH_MAX_ATTEMPTS:
                time.sleep(backoff)
                backoff *= 2
        log.error(
            'Push failed after %d attempts in %s',
            _PUSH_MAX_ATTEMPTS, self._path,
        )
        return False

    def set_active(self):
        """Update .active file with current date and push (repository keepalive)."""
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo('Africa/Windhoek')
        except (ImportError, KeyError):
            tz = datetime.timezone.utc

        today = datetime.datetime.now(tz).strftime('%A %d-%B, %Y')
        active_file = os.path.join(self._path, '.active')

        with open(active_file, 'w') as f:
            f.write(today + '\n')

        self._run(['add', '.'], cwd=self._path)
        self._run(['commit', '-am', f'active on {today}'], cwd=self._path)
        self._run(['push'], cwd=self._path)
        log.info('Set active: %s', today)

    def _run(self, args, cwd=None, timeout=_DEFAULT_TIMEOUT):
        """Run a git command and return (returncode, stdout, stderr).

        Args:
            args: Git subcommand and arguments (without the 'git' prefix).
            cwd: Working directory for the command.
            timeout: Seconds to wait before killing the process. Large
                operations like `push` on repos with 100K+ files should
                pass a higher value than the default.
        """
        cmd = ['git'] + args
        log.debug('Running: %s (cwd=%s, timeout=%ss)', ' '.join(cmd), cwd, timeout)
        env = dict(os.environ)
        try:
            result = subprocess.run(
                cmd, cwd=cwd, env=env,
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            log.warning('git %s timed out after %ss', args[0], timeout)
            return 124, '', f'git {args[0]} timed out after {timeout}s'
        if result.returncode != 0:
            log.warning('git %s failed: %s', args[0], result.stderr.strip())
        return result.returncode, result.stdout.strip(), result.stderr.strip()


def push_all_repos(scripture_repo, hash_repo):
    """Push both scripture and hash repositories.

    Args:
        scripture_repo: GitRepository for scripture data.
        hash_repo: GitRepository for public hash data.

    Returns:
        True if any changes were pushed.
    """
    pushed = False
    for repo in [scripture_repo, hash_repo]:
        if repo.exists and repo.push():
            pushed = True
    return pushed


# ── Backward-compatible module-level functions ───────────────────────────────

def _run_git(args, cwd=None):
    """Run a git command and return (returncode, stdout, stderr)."""
    repo = GitRepository('.')
    return repo._run(args, cwd=cwd)


def prep_git_repo(repo_path, repo_url=None, pull=False, preserve_extras=None):
    """Prepare a git repository. See GitRepository.prepare."""
    repo = GitRepository(repo_path, repo_url)
    repo.prepare(pull=pull, preserve_extras=preserve_extras)


def push_to_github(repo_path, commit_message='Update'):
    """Stage, commit, push. See GitRepository.push."""
    return GitRepository(repo_path).push(commit_message)


def push_all(api_path):
    """Push both scripture and hash repositories."""
    scripture_path = api_path + '_scripture'
    pushed = False
    for path in [scripture_path, api_path]:
        if os.path.isdir(path):
            if GitRepository(path).push():
                pushed = True
    return pushed


def set_active(repo_path):
    """Update .active and push. See GitRepository.set_active."""
    GitRepository(repo_path).set_active()
