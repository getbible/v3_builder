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

from publication_safety import (
    DEFAULT_MAX_GROWTH_RATIO,
    MAX_PUBLISHED_FILE_BYTES,
    PublicationSafetyError,
    validate_generated_output,
)

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


class GitOperationError(RuntimeError):
    """Raised when a required Git operation fails."""

    def __init__(self, operation, path, stderr=""):
        detail = f": {stderr}" if stderr else ""
        super().__init__(f"git {operation} failed in {path}{detail}")
        self.operation = operation
        self.path = path
        self.stderr = stderr


class GitPushError(GitOperationError):
    """Raised when a required push fails permanently or exhausts retries."""

    def __init__(self, path, stderr, *, attempts, permanent):
        super().__init__("push", path, stderr)
        self.attempts = attempts
        self.permanent = permanent


_PERMANENT_PUSH_FAILURE_MARKERS = (
    "gh001:",
    "gh006:",
    "gh013:",
    "large files detected",
    "exceeds github's file size limit",
    "pre-receive hook declined",
    "protected branch hook declined",
    "remote rejected",
    "permission denied",
    "authentication failed",
    "access denied",
    "write access to repository not granted",
    "requested url returned error: 401",
    "requested url returned error: 403",
    "repository not found",
    "could not read from remote repository",
    "does not appear to be a git repository",
    "src refspec",
    "updates were rejected",
    "non-fast-forward",
)


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

    def __init__(
        self,
        path,
        repo_url=None,
        *,
        allow_output_growth=False,
        max_file_bytes=MAX_PUBLISHED_FILE_BYTES,
        max_growth_ratio=DEFAULT_MAX_GROWTH_RATIO,
    ):
        self._path = path
        self._repo_url = repo_url
        self._allow_output_growth = allow_output_growth
        self._max_file_bytes = max_file_bytes
        self._max_growth_ratio = max_growth_ratio

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
            True if changes were pushed, False if nothing changed.

        Raises:
            PublicationSafetyError: Generated output fails a publication gate.
            GitOperationError: A required status, stage, commit, or push fails.
        """
        if not self.has_git:
            raise GitOperationError('repository validation', self._path, 'missing .git directory')

        self.validate_output()

        rc, stdout, stderr = self._run(['status', '--porcelain'], cwd=self._path)
        if rc != 0:
            raise GitOperationError('status', self._path, stderr)
        if not stdout:
            log.info('Nothing to commit in %s', self._path)
            return False

        rc, _, stderr = self._run(['add', '.'], cwd=self._path)
        if rc != 0:
            raise GitOperationError('add', self._path, stderr)
        rc, _, stderr = self._run(['commit', '-am', commit_message], cwd=self._path)
        if rc != 0:
            raise GitOperationError('commit', self._path, stderr)

        self._push_with_retry()

        log.info('Pushed changes from %s', self._path)
        return True

    def validate_output(self):
        """Validate generated files against hard and historical size gates."""
        baseline = self._tracked_json_sizes() if self.has_git else {}
        if self._allow_output_growth:
            log.warning(
                'Tracked JSON growth gate explicitly overridden for %s; '
                'the %.2f MiB hard file ceiling remains enforced',
                self._path,
                self._max_file_bytes / (1024 * 1024),
            )
        return validate_generated_output(
            self._path,
            baseline_json_sizes=baseline,
            allow_growth=self._allow_output_growth,
            max_file_bytes=self._max_file_bytes,
            max_growth_ratio=self._max_growth_ratio,
            preserved_names=self._PRESERVE,
        )

    def _tracked_json_sizes(self):
        """Return exact byte sizes of JSON blobs tracked at ``HEAD``."""
        rc, _, _ = self._run(
            ['rev-parse', '--verify', 'HEAD'], cwd=self._path,
        )
        if rc != 0:
            return {}
        rc, stdout, stderr = self._run(
            ['ls-tree', '-r', '-l', '-z', 'HEAD'], cwd=self._path,
        )
        if rc != 0:
            raise PublicationSafetyError(
                f'could not read publication baseline from {self._path}: {stderr}'
            )

        sizes = {}
        for record in stdout.split('\0'):
            if not record:
                continue
            metadata, separator, path = record.partition('\t')
            fields = metadata.split()
            if not separator or len(fields) != 4 or fields[1] != 'blob':
                continue
            if not path.endswith('.json'):
                continue
            try:
                sizes[path] = int(fields[3])
            except ValueError as exc:
                raise PublicationSafetyError(
                    f'invalid Git blob size for {path!r} in {self._path}: {fields[3]!r}'
                ) from exc
        return sizes

    def _push_with_retry(self):
        """Run `git push` with a large-repo timeout and retry on failure.

        Pushing the scripture/hash repositories can involve 100K+ files
        and take many minutes. A single attempt may time out or hit a
        transient network error, so we retry with exponential backoff.

        Raises:
            GitPushError: The failure is permanent or retries are exhausted.
        """
        backoff = _PUSH_INITIAL_BACKOFF
        last_stderr = ''
        for attempt in range(1, _PUSH_MAX_ATTEMPTS + 1):
            rc, _, stderr = self._run(
                ['push'], cwd=self._path, timeout=_PUSH_TIMEOUT,
            )
            if rc == 0:
                return True
            last_stderr = stderr
            permanent = _is_permanent_push_failure(stderr)
            log.warning(
                'Push attempt %d/%d failed in %s: %s',
                attempt, _PUSH_MAX_ATTEMPTS, self._path, stderr,
            )
            if permanent:
                log.error('Push failure is permanent; retries suppressed in %s', self._path)
                raise GitPushError(
                    self._path, stderr, attempts=attempt, permanent=True,
                )
            if attempt < _PUSH_MAX_ATTEMPTS:
                time.sleep(backoff)
                backoff *= 2
        log.error(
            'Push failed after %d attempts in %s',
            _PUSH_MAX_ATTEMPTS, self._path,
        )
        raise GitPushError(
            self._path,
            last_stderr,
            attempts=_PUSH_MAX_ATTEMPTS,
            permanent=False,
        )

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
        True if any changes were pushed, False if neither repository changed.

    The Scripture repository is required and is always completed first. Any
    failure raises immediately, so the hash repository is never attempted after
    a Scripture publication failure.
    """
    # Complete all non-mutating publication checks before either repository is
    # committed or pushed. This prevents a known-bad hash tree from being
    # discovered only after Scripture has already reached its remote.
    scripture_repo.validate_output()
    hash_repo.validate_output()

    scripture_pushed = scripture_repo.push()
    hash_pushed = hash_repo.push()
    return scripture_pushed or hash_pushed


def _is_permanent_push_failure(stderr):
    """Whether retrying the same push cannot resolve the reported failure."""
    normalized = (stderr or '').lower()
    return any(marker in normalized for marker in _PERMANENT_PUSH_FAILURE_MARKERS)


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
    return push_all_repos(
        GitRepository(scripture_path),
        GitRepository(api_path),
    )


def set_active(repo_path):
    """Update .active and push. See GitRepository.set_active."""
    GitRepository(repo_path).set_active()
