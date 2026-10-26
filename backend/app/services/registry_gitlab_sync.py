"""
GitLab repository sync helper for Registry monitor.
Uses git CLI with HTTPS token auth and a local cache directory.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote, urlsplit, urlunsplit

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class RegistryGitlabSyncResult:
    repo_name: str
    repo_url: str
    repo_ref: str
    repo_commit: Optional[str]
    repo_dir: str
    status: str
    started_at: datetime
    finished_at: datetime
    error: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class RegistryGitlabSync:
    """Clone/fetch GitLab repositories into the configured cache directory."""

    def __init__(self) -> None:
        self.cache_dir = Path(settings.repo_cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _sanitize_repo_name(name: str) -> str:
        value = re.sub(r"[^A-Za-z0-9_.-]+", "-", name.strip())
        return value.strip("-") or "registry-repo"

    @staticmethod
    def _is_git_ssh_url(url: str) -> bool:
        return url.startswith("git@") or url.startswith("ssh://")

    def _rewrite_url_for_https_token(self, repo_url: str) -> str:
        username = (settings.gitlab_username or "").strip()
        token = (settings.gitlab_token or "").strip()
        if not username or not token:
            return repo_url

        if self._is_git_ssh_url(repo_url):
            # git@gitlab.example:group/repo.git -> https://gitlab.example/group/repo.git
            if repo_url.startswith("git@") and ":" in repo_url:
                _, rest = repo_url.split("@", 1)
                host, path = rest.split(":", 1)
                repo_url = f"https://{host}/{path}"
            elif repo_url.startswith("ssh://"):
                parsed = urlsplit(repo_url)
                repo_url = urlunsplit(("https", parsed.hostname or "", parsed.path or "", parsed.query, parsed.fragment))

        parsed = urlsplit(repo_url)
        if not parsed.scheme:
            return repo_url
        host = parsed.hostname or ""
        if not host:
            return repo_url
        port = f":{parsed.port}" if parsed.port else ""
        encoded_username = quote(username, safe="")
        encoded_token = quote(token, safe="")
        netloc = f"{encoded_username}:{encoded_token}@{host}{port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))

    @staticmethod
    def _run_git(args: list[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )

    def _resolve_default_branch(self, repo_dir: Path) -> Optional[str]:
        try:
            result = self._run_git(["git", "-C", str(repo_dir), "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"])
            ref = result.stdout.strip()
            if ref.startswith("origin/"):
                return ref.split("/", 1)[1]
            if ref:
                return ref
        except Exception:
            pass
        try:
            result = self._run_git(["git", "-C", str(repo_dir), "remote", "show", "origin"])
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.startswith("HEAD branch:"):
                    branch = line.split(":", 1)[1].strip()
                    return branch or None
        except Exception:
            pass
        return None

    def _remote_branch_exists(self, repo_dir: Path, branch: str) -> bool:
        try:
            self._run_git(["git", "-C", str(repo_dir), "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}"])
            return True
        except subprocess.CalledProcessError:
            return False

    def _clone_or_fetch(self, repo_dir: Path, repo_url: str) -> None:
        if not repo_dir.exists():
            self._run_git(["git", "clone", "--depth=1", repo_url, str(repo_dir)])
            return
        if not (repo_dir / ".git").exists():
            self._run_git(["git", "-C", str(repo_dir), "init"])
            try:
                self._run_git(["git", "-C", str(repo_dir), "remote", "add", "origin", repo_url])
            except subprocess.CalledProcessError:
                self._run_git(["git", "-C", str(repo_dir), "remote", "set-url", "origin", repo_url])
            self._run_git(["git", "-C", str(repo_dir), "fetch", "origin", "--depth=1", "--prune", "--tags", "--force"])
            return
        self._run_git(["git", "-C", str(repo_dir), "remote", "set-url", "origin", repo_url])
        self._run_git(["git", "-C", str(repo_dir), "fetch", "origin", "--depth=1", "--prune", "--tags", "--force"])

    def sync_repo(self, repo_cfg: Dict[str, Any]) -> RegistryGitlabSyncResult:
        repo_name = str(repo_cfg.get("name") or repo_cfg.get("repo_name") or "registry-repo").strip() or "registry-repo"
        repo_url = str(repo_cfg.get("url") or "").strip()
        repo_ref = str(repo_cfg.get("ref") or "HEAD").strip() or "HEAD"
        started_at = datetime.now(timezone.utc)
        finished_at = started_at
        repo_commit: Optional[str] = None
        status = "success"
        error: Optional[str] = None
        details: Dict[str, Any] = {}

        if not repo_url:
            return RegistryGitlabSyncResult(
                repo_name=repo_name,
                repo_url=repo_url,
                repo_ref=repo_ref,
                repo_commit=None,
                repo_dir=str(self.cache_dir / self._sanitize_repo_name(repo_name)),
                status="failed",
                started_at=started_at,
                finished_at=finished_at,
                error="Repository URL is empty",
                details={"error": "Repository URL is empty"},
            )

        repo_dir = self.cache_dir / self._sanitize_repo_name(repo_name)
        auth_url = self._rewrite_url_for_https_token(repo_url)
        try:
            repo_dir.parent.mkdir(parents=True, exist_ok=True)
            self._clone_or_fetch(repo_dir, auth_url)
            checkout_ref = repo_ref
            if repo_ref.upper() == "HEAD":
                checkout_ref = self._resolve_default_branch(repo_dir) or "HEAD"
            if checkout_ref != "HEAD" and self._remote_branch_exists(repo_dir, checkout_ref):
                self._run_git(["git", "-C", str(repo_dir), "checkout", "-B", checkout_ref, f"origin/{checkout_ref}"])
            else:
                self._run_git(["git", "-C", str(repo_dir), "checkout", "-f", checkout_ref])
            commit_result = self._run_git(["git", "-C", str(repo_dir), "rev-parse", "HEAD"])
            repo_commit = commit_result.stdout.strip() or None
            details = {
                "checkout_ref": checkout_ref,
                "repo_dir": str(repo_dir),
            }
            finished_at = datetime.now(timezone.utc)
        except subprocess.CalledProcessError as exc:
            status = "failed"
            finished_at = datetime.now(timezone.utc)
            error = f"Git command failed (exit {exc.returncode})"
            details = {
                "error": error,
                "repo_dir": str(repo_dir),
            }
            logger.warning("Git sync failed for %s: %s", repo_name, error)
        except Exception as exc:
            status = "failed"
            finished_at = datetime.now(timezone.utc)
            error = f"Git synchronization failed ({type(exc).__name__})"
            details = {
                "error": error,
                "repo_dir": str(repo_dir),
            }
            logger.warning("Git sync failed for %s: %s", repo_name, error)

        return RegistryGitlabSyncResult(
            repo_name=repo_name,
            repo_url=repo_url,
            repo_ref=repo_ref,
            repo_commit=repo_commit,
            repo_dir=str(repo_dir),
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            error=error,
            details=details,
        )
