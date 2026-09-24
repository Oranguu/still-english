"""Inspect staged/committed blobs, never private working-directory files."""
import argparse
import re
import subprocess
import sys
from pathlib import Path


PUBLIC_FILES = frozenset({
    ".gitignore", "README.md", "LICENSE", "CONTRIBUTING.md", "SECURITY.md",
    "tool/.gitignore", "tool/README.md", "tool/requirements.txt",
    "tool/ai.py", "tool/captions.py", "tool/jobs.py", "tool/launcher.py",
    "tool/packages.py", "tool/server.py", "tool/setup.py", "tool/transcribe.py",
    "tool/启动.command", "tool/安装依赖.command", "tool/安装语音转写.command",
    "tool/static/index.html", "tool/static/app.js", "tool/static/style.css",
    "tool/static/favicon.svg", "tool/tests/test_core.py", "tool/tests/test_server.py",
    "tool/tests/test_public_files.py", "tool/scripts/check_public_files.py",
})
MAX_BYTES = 250_000
PATTERNS = {
    "OpenAI-style credential": r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}\b",
    "GitHub credential": r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b",
    "JWT credential": r"\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}",
    "private key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "AWS credential": r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    "bearer credential": r"(?i)\bBearer\s+[A-Za-z0-9_.-]{24,}",
    "credential assignment": r'''(?i)["']?(?:access_token|refresh_token|id_token|api_key|client_secret|password)["']?\s*[:=]\s*["'][A-Za-z0-9_./+=-]{20,}["']''',
    "personal home directory": r"(?:/Users/|/home/)[A-Za-z0-9._-]+/|[A-Za-z]:[\\/]Users[\\/][A-Za-z0-9._-]+[\\/]",
}


def check_blob(path, mode, data):
    """Return only labels/line numbers; never echo suspected sensitive values."""
    if path not in PUBLIC_FILES:
        return ["file is not on the public source allowlist"]
    if mode not in {"100644", "100755"}:
        return ["symlinks and submodules must not be published"]
    if len(data) > MAX_BYTES:
        return ["file exceeds the reviewed source size limit"]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return ["binary file must not be published"]
    if "\0" in text:
        return ["binary file must not be published"]
    issues = []
    for label, pattern in PATTERNS.items():
        match = re.search(pattern, text)
        if match:
            line = text.count("\n", 0, match.start()) + 1
            issues.append(f"{label} at line {line}")
    return issues


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.PIPE)


def audit_repository(root, tree=None):
    if tree:
        revision = git(root, "rev-parse", "--verify", "--end-of-options", tree + "^{tree}").decode().strip()
        records = git(root, "ls-tree", "-r", "-z", revision)
    else:
        records = git(root, "ls-files", "--stage", "-z")
    issues, count = [], 0
    for record in records.split(b"\0"):
        if not record:
            continue
        meta, raw_path = record.split(b"\t", 1)
        first, second, third = meta.decode().split()
        mode, oid = first, third if tree else second
        path = raw_path.decode("utf-8", errors="replace")
        count += 1
        if not tree and third != "0":
            issues.append((path, "unresolved merge entry"))
            continue
        # Reject unknown paths before reading blobs (which may be huge videos).
        if path not in PUBLIC_FILES or mode not in {"100644", "100755"}:
            issues.extend((path, reason) for reason in check_blob(path, mode, b""))
            continue
        size = int(git(root, "cat-file", "-s", oid))
        if size > MAX_BYTES:
            issues.append((path, "file exceeds the reviewed source size limit"))
            continue
        issues.extend((path, reason) for reason in check_blob(path, mode, git(root, "cat-file", "blob", oid)))
    if not count:
        issues.append(("repository", "no staged or committed files to inspect"))
    return count, issues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", help="inspect a commit/tree instead of the Git index")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    try:
        count, issues = audit_repository(root, args.tree)
    except (OSError, ValueError, subprocess.CalledProcessError):
        print("Unable to inspect Git objects; initialize the repository and stage source files first.", file=sys.stderr)
        return 2
    for path, reason in issues:
        print(f"BLOCKED: {path}: {reason}", file=sys.stderr)
    if issues:
        return 1
    target = "committed tree" if args.tree else "Git index"
    print(f"PASS: {count} public source files checked in {target}. Review the diff before publishing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
