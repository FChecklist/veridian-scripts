"""Real regression test for quality-gate.sh's DOCS_ONLY classifier.

History, both against the REAL script (never reimplemented/mocked):

1. AUDIT:FAIL on PR #305's first head (b315ae9618e311f5307749fac025eba0ec8b739a)
   caught the original code-relevant-extension BLOCKLIST wrongly classifying
   diffs touching ONLY requirements.txt, pyproject.toml, a CI/lint config
   (ci.yml, .eslintrc.yml), or a bare Dockerfile as docs-only, silently
   skipping the node/python lint-build-test gates for changes that genuinely
   needed them.
2. A second, independent AUDIT:FAIL on the very next head (a63def8e) proved
   the fix for (1) was still the same shape of bug: the blocklist had grown
   to cover the specific extensions/filenames already caught, but setup.cfg,
   tox.ini, pytest.ini, yarn.lock, Cargo.lock, and poetry.lock -- and any
   other extension nobody had thought to add yet -- still slipped through
   misclassified DOCS_ONLY=1.

The fix inverts the check to a small, closed docs-only ALLOWLIST (prose,
docs/, LICENSE, images) so anything NOT on it -- including every case above
plus any future unrecognized extension -- fails closed to code-relevant
(gates run), never open to docs-only (gates skipped).

Two layers:

1. `DocsOnlyDetectionPatternTest` extracts the two live allowlist regexes
   straight out of quality-gate.sh itself (so this test cannot silently
   drift from whatever is actually shipped) and reproduces the real
   `grep -qvE` logic, via real `grep` subprocess calls, against the exact
   filenames both audits called out plus the required extended coverage.
2. `DocsOnlyEndToEndTest` runs the real quality-gate.sh as a subprocess
   against real temp git repos, proving the end-to-end skip/non-skip
   behavior, not just the isolated regex match.
"""
import os
import re
import subprocess
import tempfile
import unittest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUALITY_GATE = os.path.join(SCRIPTS_DIR, "quality-gate.sh")


def _real_docs_only_patterns():
    """Pulls the two exact docs-only allowlist regexes the live DOCS_ONLY
    check uses out of quality-gate.sh itself, in source order."""
    with open(QUALITY_GATE) as f:
        src = f.read()
    ext = re.search(r"DOCS_ONLY_EXT_PATTERN='((?:[^'\\]|\\.)*)'", src)
    name = re.search(r"DOCS_ONLY_NAME_PATTERN='((?:[^'\\]|\\.)*)'", src)
    assert ext and name, (
        "expected DOCS_ONLY_EXT_PATTERN and DOCS_ONLY_NAME_PATTERN single-quoted "
        "assignments in quality-gate.sh's DOCS_ONLY block -- has the detection "
        "logic changed shape?"
    )
    return ext.group(1), name.group(1)


def _is_code_relevant(ext_pattern, name_pattern, filenames):
    """Reproduces the real DOCS_ONLY boolean (is this file list code-relevant,
    i.e. NOT docs-only) using the real, live allowlist patterns against a
    real newline-joined file list via `grep -qvE`, exactly as
    quality-gate.sh itself does: code-relevant iff at least one changed file
    does NOT match either allowlist pattern (fails closed)."""
    joined = "\n".join(filenames) + "\n"
    combined = "{}|{}".format(ext_pattern, name_pattern)
    return subprocess.run(["grep", "-qvE", combined], input=joined, text=True).returncode == 0


class DocsOnlyDetectionPatternTest(unittest.TestCase):
    """Table-driven check of the real, live allowlist regexes against the
    exact filenames both AUDIT:FAIL comments on PR #305 called out as
    misclassified, plus the pre-existing and newly-required cases that must
    keep working."""

    def setUp(self):
        self.ext_pattern, self.name_pattern = _real_docs_only_patterns()

    def _assert_code_relevant(self, filename):
        self.assertTrue(
            _is_code_relevant(self.ext_pattern, self.name_pattern, [filename]),
            "%r must be classified code-relevant (gates must run)" % filename,
        )

    def _assert_docs_only(self, filename):
        self.assertFalse(
            _is_code_relevant(self.ext_pattern, self.name_pattern, [filename]),
            "%r must be classified docs-only (gates may be skipped)" % filename,
        )

    # -- first AUDIT:FAIL (head b315ae9) --
    def test_requirements_txt_is_code_relevant(self):
        self._assert_code_relevant("requirements.txt")

    def test_pyproject_toml_is_code_relevant(self):
        self._assert_code_relevant("pyproject.toml")

    def test_ci_yml_is_code_relevant(self):
        self._assert_code_relevant(".github/workflows/ci.yml")

    def test_eslintrc_yml_is_code_relevant(self):
        self._assert_code_relevant(".eslintrc.yml")

    def test_bare_dockerfile_is_code_relevant(self):
        self._assert_code_relevant("Dockerfile")

    def test_nested_dockerfile_variant_is_code_relevant(self):
        self._assert_code_relevant("docker/Dockerfile.prod")

    def test_makefile_is_code_relevant(self):
        self._assert_code_relevant("Makefile")

    # -- second AUDIT:FAIL (head a63def8e) --
    def test_setup_cfg_is_code_relevant(self):
        self._assert_code_relevant("setup.cfg")

    def test_tox_ini_is_code_relevant(self):
        self._assert_code_relevant("tox.ini")

    def test_pytest_ini_is_code_relevant(self):
        self._assert_code_relevant("pytest.ini")

    def test_yarn_lock_is_code_relevant(self):
        self._assert_code_relevant("yarn.lock")

    def test_cargo_lock_is_code_relevant(self):
        self._assert_code_relevant("Cargo.lock")

    def test_poetry_lock_is_code_relevant(self):
        self._assert_code_relevant("poetry.lock")

    # -- required extended coverage (allowlist inversion, this fix) --
    def test_package_lock_json_is_code_relevant(self):
        self._assert_code_relevant("package-lock.json")

    def test_gemfile_lock_is_code_relevant(self):
        self._assert_code_relevant("Gemfile.lock")

    def test_go_sum_is_code_relevant(self):
        self._assert_code_relevant("go.sum")

    def test_gitlab_ci_yml_is_code_relevant(self):
        self._assert_code_relevant(".gitlab-ci.yml")

    def test_renovate_json_is_code_relevant(self):
        self._assert_code_relevant("renovate.json")

    def test_never_before_seen_extension_is_code_relevant(self):
        """The whole point of the allowlist inversion: an extension this
        script has never heard of must fail closed to code-relevant, not
        fail open to docs-only."""
        self._assert_code_relevant("foo.zzz")

    # -- docs-only optimisation must not be destroyed --
    def test_pure_docs_diff_is_not_code_relevant(self):
        self.assertFalse(_is_code_relevant(self.ext_pattern, self.name_pattern, ["PROGRESS.md", "README.md"]))

    def test_readme_md_is_docs_only(self):
        self._assert_docs_only("README.md")

    def test_docs_dir_md_is_docs_only(self):
        self._assert_docs_only("docs/whatever.md")

    def test_mixed_diff_is_code_relevant(self):
        self.assertTrue(_is_code_relevant(self.ext_pattern, self.name_pattern, ["PROGRESS.md", "quality-gate.sh"]))

    # -- root-caused 2026-08-14 (task-20260814-062124, RCA of UMR-43d0):
    # ai-os governance bookkeeping files that AGENTS.md Rule 11 / this repo's
    # own conventions require every docs-only task to touch (claim register,
    # doc index) are still .yaml, so they were wrongly classified
    # code-relevant, forcing a full build that then failed on real build-lock
    # contention -- for a diff that touched zero code.
    def test_active_claims_yaml_is_docs_only(self):
        self._assert_docs_only("ai-os/boss/ACTIVE-CLAIMS.yaml")

    def test_os_yaml_is_docs_only(self):
        self._assert_docs_only("ai-os/OS.yaml")

    def test_rca_task_diff_shape_is_docs_only(self):
        self.assertFalse(_is_code_relevant(self.ext_pattern, self.name_pattern, [
            "ai-os/OS.yaml",
            "ai-os/boss/ACTIVE-CLAIMS.yaml",
            "ai-os/registry/UMR-20260808-150937-43d0-RCA.md",
            "progress/task-20260814-062124-rca--umr-20260808-150937-43d0-killed.md",
        ]))

    def test_other_ai_os_yaml_is_still_code_relevant(self):
        """The allowlist addition is deliberately narrow (two specific,
        verified-safe paths), not `ai-os/**/*.yaml` generally -- any other
        yaml under ai-os/ must still fail closed."""
        self._assert_code_relevant("ai-os/MASTER-TRACKER.yaml")
        self._assert_code_relevant("ai-os/engines/ENGINES.yaml")

    def test_unrelated_top_level_yaml_is_still_code_relevant(self):
        self._assert_code_relevant("OS.yaml")
        self._assert_code_relevant("ACTIVE-CLAIMS.yaml")


class DocsOnlyEndToEndTest(unittest.TestCase):
    """Full subprocess runs of the real quality-gate.sh against real git
    repos -- proves the end-to-end DOCS_ONLY skip and non-skip behavior, not
    just the isolated regex match."""

    def _make_repo_with_diff(self, base_files, branch_files):
        """Real upstream repo (initial commit on `main`) + a real clone of it
        (so `origin/main` exists to diff against, matching how this script
        is actually invoked inside a worker's own git checkout), with a
        second commit on top of the clone's main containing branch_files."""
        upstream = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", "-b", "main", upstream], check=True)
        for name, content in base_files.items():
            path = os.path.join(upstream, name)
            os.makedirs(os.path.dirname(path) or upstream, exist_ok=True)
            with open(path, "w") as f:
                f.write(content)
        subprocess.run(["git", "-C", upstream, "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", upstream, "-c", "user.email=t@t.com", "-c", "user.name=t",
             "commit", "-q", "-m", "base"],
            check=True,
        )

        clone = tempfile.mkdtemp()
        subprocess.run(["git", "clone", "-q", upstream, clone], check=True)
        for name, content in branch_files.items():
            path = os.path.join(clone, name)
            os.makedirs(os.path.dirname(path) or clone, exist_ok=True)
            with open(path, "w") as f:
                f.write(content)
        subprocess.run(["git", "-C", clone, "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", clone, "-c", "user.email=t@t.com", "-c", "user.name=t",
             "commit", "-q", "-m", "branch change"],
            check=True,
        )
        return clone

    def _run_gate(self, workspace):
        out = os.path.join(workspace, "gate-out.json")
        return subprocess.run(
            ["bash", QUALITY_GATE, workspace, out],
            capture_output=True, text=True, timeout=60,
        )

    def test_pure_docs_diff_skips_gates(self):
        workspace = self._make_repo_with_diff(
            {"README.md": "hello\n"}, {"PROGRESS.md": "done\n"}
        )
        proc = self._run_gate(workspace)
        self.assertIn("skipping node/python lint/build/test gates", proc.stdout)

    def test_docs_dir_diff_skips_gates(self):
        workspace = self._make_repo_with_diff(
            {"README.md": "hello\n"}, {"docs/whatever.md": "notes\n"}
        )
        proc = self._run_gate(workspace)
        self.assertIn("skipping node/python lint/build/test gates", proc.stdout)

    def test_requirements_txt_only_diff_does_not_skip_gates(self):
        workspace = self._make_repo_with_diff(
            {"README.md": "hello\n", "requirements.txt": "flask==1.0\n"},
            {"requirements.txt": "flask==2.0\n"},
        )
        proc = self._run_gate(workspace)
        self.assertNotIn("skipping node/python lint/build/test gates", proc.stdout)
        self.assertIn("Detected Python project", proc.stdout)

    def test_dockerfile_only_diff_does_not_skip_gates(self):
        workspace = self._make_repo_with_diff(
            {"README.md": "hello\n", "requirements.txt": "flask==1.0\n"},
            {"Dockerfile": "FROM python:3.12\n"},
        )
        proc = self._run_gate(workspace)
        self.assertNotIn("skipping node/python lint/build/test gates", proc.stdout)

    def test_setup_cfg_only_diff_does_not_skip_gates(self):
        workspace = self._make_repo_with_diff(
            {"README.md": "hello\n", "requirements.txt": "flask==1.0\n"},
            {"setup.cfg": "[bdist_wheel]\nuniversal=1\n"},
        )
        proc = self._run_gate(workspace)
        self.assertNotIn("skipping node/python lint/build/test gates", proc.stdout)

    def test_yarn_lock_only_diff_does_not_skip_gates(self):
        workspace = self._make_repo_with_diff(
            {"README.md": "hello\n", "package.json": "{}\n"},
            {"yarn.lock": "# yarn lockfile v1\n"},
        )
        proc = self._run_gate(workspace)
        self.assertNotIn("skipping node/python lint/build/test gates", proc.stdout)

    def test_ai_os_governance_yaml_diff_skips_gates(self):
        """Root-caused 2026-08-14 (task-20260814-062124, RCA of UMR-43d0):
        this exact diff shape (new registry doc + the two governance
        bookkeeping files every such task is required to touch) was
        wrongly paying the full build gate and failing on real build-lock
        contention for a change that touched zero code."""
        workspace = self._make_repo_with_diff(
            {"README.md": "hello\n"},
            {
                "ai-os/OS.yaml": "docs: []\n",
                "ai-os/boss/ACTIVE-CLAIMS.yaml": "claims: []\n",
                "ai-os/registry/UMR-EXAMPLE-RCA.md": "# RCA\n",
            },
        )
        proc = self._run_gate(workspace)
        self.assertIn("skipping node/python lint/build/test gates", proc.stdout)

    def test_other_ai_os_yaml_diff_does_not_skip_gates(self):
        workspace = self._make_repo_with_diff(
            {"README.md": "hello\n"}, {"ai-os/MASTER-TRACKER.yaml": "open: []\n"}
        )
        proc = self._run_gate(workspace)
        self.assertNotIn("skipping node/python lint/build/test gates", proc.stdout)

    def test_unknown_extension_only_diff_does_not_skip_gates(self):
        """The core allowlist-inversion guarantee end-to-end: an extension
        this script has never heard of must not silently skip gates."""
        workspace = self._make_repo_with_diff(
            {"README.md": "hello\n", "requirements.txt": "flask==1.0\n"},
            {"foo.zzz": "whatever\n"},
        )
        proc = self._run_gate(workspace)
        self.assertNotIn("skipping node/python lint/build/test gates", proc.stdout)


if __name__ == "__main__":
    unittest.main()
