#!/usr/bin/env python3
"""
chatgpt_audit_guard.py -- mechanical write-scope enforcement for the ChatGPT
Audit Workspace (INS-20260724-141101-89d8, task-20260724-141323-chatgpt-audit-
workspace-infrastructure).

This is the actual guarantee, not a policy statement, that "ChatGPT never
modifies production source code": any code path that wants to write an
audit-generated file MUST route the write through guarded_write()/
guarded_open() below, which resolves the real path (following symlinks,
'..', relative segments) and refuses anything outside ALLOWED_ROOT before a
single byte touches disk. There is no bypass flag -- a caller that needs to
write somewhere else is, by definition, not doing ChatGPT-audit work.

CLI:
  --test-path PATH   check only, nothing written. Exit 0 = allowed, 1 = rejected.
  write --path PATH (--content-file FILE | --stdin)
                      perform a real guarded write. Exit 0 on success,
                      1 if PATH is outside ALLOWED_ROOT (nothing written).

Run: python3 scripts/chatgpt_audit_guard.py --test-path <path>
"""
import argparse
import os
import sys

ALLOWED_ROOT = "/opt/veridian/chatgpt-audit"


class PathNotAllowed(PermissionError):
    pass


def _resolved_allowed_root():
    return os.path.realpath(ALLOWED_ROOT)


def is_path_allowed(path):
    """True iff the *resolved* path sits inside ALLOWED_ROOT. Resolves the
    real, absolute path first (os.path.realpath) so a relative path, a
    trailing '..', or a symlink pointing outside ALLOWED_ROOT is caught --
    a plain string-prefix check is not enough (e.g.
    '/opt/veridian/chatgpt-audit-evil' shares the string prefix
    '/opt/veridian/chatgpt-audit' without being inside it)."""
    root = _resolved_allowed_root()
    target = os.path.realpath(os.path.abspath(path))
    return target == root or target.startswith(root + os.sep)


def assert_path_allowed(path):
    if not is_path_allowed(path):
        raise PathNotAllowed(
            f"REJECTED: '{path}' resolves outside the ChatGPT Audit Workspace "
            f"({ALLOWED_ROOT}). ChatGPT-audit writes are mechanically confined "
            f"to this directory -- this is not a policy reminder, the write "
            f"did not happen."
        )


def guarded_open(path, mode="w", **kwargs):
    """Drop-in replacement for open() that refuses any path outside
    ALLOWED_ROOT before touching the filesystem. Use this (or guarded_write)
    for every audit-record write -- never call open() directly on a
    ChatGPT-supplied path."""
    assert_path_allowed(path)
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    return open(path, mode, **kwargs)


def guarded_write(path, content, mode="w"):
    with guarded_open(path, mode) as f:
        f.write(content)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--test-path", help="check whether PATH would be allowed; no write, exit 0/1")
    sub = parser.add_subparsers(dest="cmd")

    p_write = sub.add_parser("write", help="perform a real guarded write")
    p_write.add_argument("--path", required=True)
    p_write.add_argument("--content-file", help="read content from this file")
    p_write.add_argument("--stdin", action="store_true", help="read content from stdin")

    args = parser.parse_args()

    if args.test_path:
        allowed = is_path_allowed(args.test_path)
        root = _resolved_allowed_root()
        target = os.path.realpath(os.path.abspath(args.test_path))
        if allowed:
            print(f"ALLOWED: {target} is inside {root}")
            sys.exit(0)
        print(f"REJECTED: {target} is outside {root}")
        sys.exit(1)

    if args.cmd == "write":
        if bool(args.content_file) == bool(args.stdin):
            print("ERROR: exactly one of --content-file or --stdin is required", file=sys.stderr)
            sys.exit(2)
        content = sys.stdin.read() if args.stdin else open(args.content_file, encoding="utf-8").read()
        try:
            guarded_write(args.path, content)
        except PathNotAllowed as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        print(f"WROTE: {args.path}")
        sys.exit(0)

    parser.print_help()
    sys.exit(2)


if __name__ == "__main__":
    main()
