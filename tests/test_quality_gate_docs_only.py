"""Real regression test for the AUDIT:FAIL finding posted on PR #305 (head
b315ae9618e311f5307749fac025eba0ec8b739a): quality-gate.sh's DOCS_ONLY
code-relevant-extension check wrongly classified diffs touching ONLY
requirements.txt, pyproject.toml, a CI/lint config (ci.yml, .eslintrc.yml),
or a bare Dockerfile as docs-only, silently skipping the node/python
lint-build-test gates for changes that genuinely needed them -- exactly the
failure mode this script's own comment says "must never risk".

Two layers, both against the REAL script (never reimplemented/mocked):

1. `DocsOnlyDetectionPatternTest` extracts the two live grep -E patterns
   straight out of quality-gate.sh itself (so this test cannot silently
   drift from whatever is actually shipped) and runs them, via real `grep`
   subprocess calls, against the exact filenames the audit called out.
2. `DocsOnlyEndToEndTest` runs the real quality-gate.sh as a subprocess
   against real temp git repos, proving the end-to-end skip/non-skip
   behavior the audit found no test coverage for at all.
"""
import os
import re
import subprocess
import tempfile
import unittest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUALITY_GATE = os.path.join(SCRIPTS_DIR, "quality-gate.sh")


def _real_docs_only_patterns():
    """Pulls the two exact grep -E patterns the live DOCS_ONLY check uses out
    of quality-gate.sh itself, in source order."""
    with open(QUALITY_GATE) as f:
        src = f.read()
    patterns = re.findall(r"grep -qE '((?:[^'\\]|\\.)*)'", src)
    assert len(patterns) >= 2, (
        "expected at least 2 grep -qE patterns (extension + filename) in "
        "quality-gate.sh's DOCS_ONLY block -- got %d; has the detection "
        "logic changed shape?" % len(patterns)
    )
    return patterns[0], patterns[1]


def _is_code_relevant(ext_pattern, name_pattern, filenames):
    """Reproduces the real DOCS_ONLY boolean (is this file list code-relevant,
    i.e. NOT docs-only) using the real, live patterns against a real
    newline-joined file list, exactly as quality-gate.sh itself does."""
    joined = "\n".join(filenames) + "\n"
    ext_hit = subprocess.run(["grep", "-qE", ext_pattern], input=joined, text=True).returncode == 0
    name_hit = subprocess.run(["grep", "-qE", name_pattern], input=joined, text=True).returncode == 0
    return ext_hit or name_hit


class DocsOnlyDetectionPatternTest(unittest.TestCase):
    """Table-driven check of the real, live regexes against the exact
    filenames the AUDIT:FAIL comment on PR #305 called out as
    misclassified, plus the pre-existing cases that must keep working."""

    def setUp(self):
        self.ext_pattern, self.name_pattern = _real_docs_only_patterns()

    def test_requirements_txt_is_code_relevant(self):
        self.assertTrue(_is_code_relevant(self.ext_pattern, self.name_pattern, ["requirements.txt"]))

    def test_pyproject_toml_is_code_relevant(self):
        self.assertTrue(_is_code_relevant(self.ext_pattern, self.name_pattern, ["pyproject.toml"]))

    def test_ci_yml_is_code_relevant(self):
        self.assertTrue(_is_code_relevant(self.ext_pattern, self.name_pattern, [".github/workflows/ci.yml"]))

    def test_eslintrc_yml_is_code_relevant(self):
        self.assertTrue(_is_code_relevant(self.ext_pattern, self.name_pattern, [".eslintrc.yml"]))

    def test_bare_dockerfile_is_code_relevant(self):
        self.assertTrue(_is_code_relevant(self.ext_pattern, self.name_pattern, ["Dockerfile"]))

    def test_nested_dockerfile_variant_is_code_relevant(self):
        self.assertTrue(_is_code_relevant(self.ext_pattern, self.name_pattern, ["docker/Dockerfile.prod"]))

    def test_makefile_is_code_relevant(self):
        self.assertTrue(_is_code_relevant(self.ext_pattern, self.name_pattern, ["Makefile"]))

    def test_pure_docs_diff_is_not_code_relevant(self):
        self.assertFalse(_is_code_relevant(self.ext_pattern, self.name_pattern, ["PROGRESS.md", "README.md"]))

    def test_mixed_diff_is_code_relevant(self):
        self.assertTrue(_is_code_relevant(self.ext_pattern, self.name_pattern, ["PROGRESS.md", "quality-gate.sh"]))


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


if __name__ == "__main__":
    unittest.main()
