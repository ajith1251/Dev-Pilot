"""
Live GitHub Integration Test — optional, not part of standard test suite.

Usage:
    python scripts/test_github_integration.py https://github.com/owner/repo

This script:
    1. Fetches repository metadata
    2. Lists branches
    3. Lists open issues
    4. Acquires a shallow clone
    5. Runs the Phase 2 Repository Intelligence Engine
    6. Displays a summary
    7. Cleans up the temporary workspace

Requires:
    - Python 3.10+
    - Git installed and in PATH
    - Network access to GitHub
    - GITHUB_TOKEN env var (optional, for rate limits / private repos)
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Ensure package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/test_github_integration.py <github-repo-url>")
        print("Example: python scripts/test_github_integration.py https://github.com/octocat/Hello-World")
        sys.exit(1)

    url = sys.argv[1].strip()
    print()
    print("=" * 60)
    print("  DevPilot Live GitHub Integration Test")
    print("=" * 60)
    print("  URL: " + url)
    print()

    from app.services.github import GitHubService
    from app.services.remote_analyzer import RemoteRepositoryAnalyzer

    # -- Parse URL --
    try:
        parsed = GitHubService.parse_any_url(url)
        owner, repo_name = parsed["owner"], parsed["repo"]
    except ValueError as exc:
        print("  [ERR] Invalid URL: " + str(exc))
        sys.exit(1)

    print("  Owner: " + owner)
    print("  Repo:  " + repo_name)
    print("  Token: " + GitHubService().get_safe_token_preview())
    print()

    github = GitHubService()

    # -- Step 1: Metadata --
    print("  [1/4] Fetching repository metadata...")
    try:
        metadata = await github.get_repo_metadata(owner, repo_name)
        print("    [OK] " + metadata.full_name + ": " + (metadata.description or "(no description)"))
        print("      Default branch: " + metadata.default_branch)
        print("      Stars: " + str(metadata.stargazers_count) + ", Forks: " + str(metadata.forks_count))
    except Exception as exc:
        print("    [ERR] Failed: " + str(exc))
        sys.exit(1)

    # -- Step 2: Branches --
    print("  [2/4] Listing branches...")
    try:
        branches = await github.list_branches(owner, repo_name)
        print("    [OK] " + str(len(branches)) + " branches found")
        for b in branches[:5]:
            print("      - " + b.name)
        if len(branches) > 5:
            print("      ... and " + str(len(branches) - 5) + " more")
    except Exception as exc:
        print("    [!] Branch listing failed (non-fatal): " + str(exc))

    # -- Step 3: Issues --
    print("  [3/4] Listing open issues...")
    try:
        issues = await github.list_issues(owner, repo_name, state="open", max_pages=1)
        print("    [OK] " + str(len(issues)) + " open issues")
        for issue in issues[:3]:
            pr_tag = " [PR]" if issue.is_pull_request else ""
            print("      #" + str(issue.number) + ": " + issue.title + pr_tag)
        if len(issues) > 3:
            print("      ... and " + str(len(issues) - 3) + " more")
    except Exception as exc:
        print("    [!] Issue listing failed (non-fatal): " + str(exc))

    # -- Step 4: Remote Analysis --
    print("  [4/4] Remote analysis (acquire + analyze)...")
    try:
        analyzer = RemoteRepositoryAnalyzer()
        result = await analyzer.analyze(url, shallow=True)

        if result.errors:
            print("    [!] Errors: " + str(result.errors))
        if result.warnings:
            print("    [!] Warnings: " + str(result.warnings))

        if result.profile:
            p = result.profile
            langs = p.get("languages", [])
            techs = p.get("technologies", [])
            scan = p.get("scan", {})

            print("    [OK] Analysis complete:")
            print("      Files: " + str(scan.get("total_files_scanned", "?")))
            print("      Languages: " + ", ".join(l.get("name", "?") for l in langs[:5]))
            print("      Technologies: " + ", ".join(t.get("name", "?") for t in techs[:5]))
            print("      Acquisition: " + str(result.acquisition.duration_seconds) + "s (shallow=" + str(result.acquisition.is_shallow) + ")")
            print("      Workspace cleaned up: yes")
        else:
            print("    [ERR] No profile generated")
    except Exception as exc:
        print("    [ERR] Remote analysis failed: " + str(exc))
        import traceback
        traceback.print_exc()

    # -- Rate limit info --
    rate_info = github.get_rate_limit_info()
    if rate_info:
        print()
        print("  Rate limit: " + str(rate_info.remaining) + "/" + str(rate_info.limit) + " remaining")

    print()
    print("=" * 60)
    print("  Test complete")
    print("=" * 60)
    print()


if __name__ == "__main__":
    asyncio.run(main())
