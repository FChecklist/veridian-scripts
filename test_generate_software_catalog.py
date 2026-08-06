#!/usr/bin/env python3
"""Real tests for generate_software_catalog.py's 2026-08-06 extension
(UMR-20260806-035541, Owner directive "real PM cycle script registry" --
item 2's backfill): *.sh/*.mjs inclusion (previously silently excluded) and
the new, honestly-recovered-only originating_umr/script_version fields.
"""
import os
import tempfile

import generate_software_catalog as gsc


def test_shell_header_comment_extracts_leading_hash_block_after_shebang():
    text = "#!/bin/bash\n# real line one\n# real line two\necho done\n"
    doc = gsc._shell_header_comment(text)
    assert doc == "real line one\nreal line two"


def test_shell_header_comment_returns_none_with_no_leading_comment():
    text = "#!/bin/bash\necho done\n"
    assert gsc._shell_header_comment(text) is None


def test_script_docstring_handles_py_and_sh_the_same_call():
    with tempfile.TemporaryDirectory() as d:
        py_path = os.path.join(d, "foo.py")
        with open(py_path, "w") as f:
            f.write('#!/usr/bin/env python3\n"""foo.py -- a real python purpose."""\nprint(1)\n')
        sh_path = os.path.join(d, "foo.sh")
        with open(sh_path, "w") as f:
            f.write("#!/bin/bash\n# foo.sh -- a real shell purpose.\necho 1\n")
        assert gsc.script_docstring(py_path) == "foo.py -- a real python purpose."
        assert gsc.script_docstring(sh_path) == "foo.sh -- a real shell purpose."


def test_list_scripts_includes_sh_and_mjs_not_only_py():
    """Real regression test for the exact gap independently found this task:
    list_scripts() used to silently exclude every *.sh/*.mjs file."""
    with tempfile.TemporaryDirectory() as d:
        for name in ("a.py", "b.sh", "c.mjs", "d.txt", "e.bak"):
            with open(os.path.join(d, name), "w") as f:
                f.write("#!/bin/sh\necho x\n")
        orig_dirs = gsc.SCRIPT_DIRS
        try:
            gsc.SCRIPT_DIRS = [d]
            found = {os.path.basename(p) for p in gsc.list_scripts()}
        finally:
            gsc.SCRIPT_DIRS = orig_dirs
        assert found == {"a.py", "b.sh", "c.mjs"}


def test_script_originating_umr_prefers_umr_shaped_id_over_task_shaped():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "foo.py")
        with open(path, "w") as f:
            f.write("# task-20260101-000000\n# UMR-20260806-035541-abcd\n")
        assert gsc.script_originating_umr(path) == "UMR-20260806-035541-abcd"


def test_script_originating_umr_falls_back_to_task_shaped_id():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "foo.py")
        with open(path, "w") as f:
            f.write("# task-20260101-000000, no UMR here\n")
        assert gsc.script_originating_umr(path) == "task-20260101-000000"


def test_script_originating_umr_is_none_when_genuinely_not_recoverable():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "foo.py")
        with open(path, "w") as f:
            f.write("print('nothing to see here')\n")
        assert gsc.script_originating_umr(path) is None


def test_script_version_from_filename_parses_trailing_v_number():
    assert gsc.script_version_from_filename("/x/generate_pm_report_v3.py") == "v3"
    assert gsc.script_version_from_filename("/x/anthropic_openrouter_proxy_v2.py") == "v2"


def test_script_version_from_filename_none_when_no_suffix():
    assert gsc.script_version_from_filename("/x/resource_governor.py") is None


if __name__ == "__main__":
    test_shell_header_comment_extracts_leading_hash_block_after_shebang()
    test_shell_header_comment_returns_none_with_no_leading_comment()
    test_script_docstring_handles_py_and_sh_the_same_call()
    test_list_scripts_includes_sh_and_mjs_not_only_py()
    test_script_originating_umr_prefers_umr_shaped_id_over_task_shaped()
    test_script_originating_umr_falls_back_to_task_shaped_id()
    test_script_originating_umr_is_none_when_genuinely_not_recoverable()
    test_script_version_from_filename_parses_trailing_v_number()
    test_script_version_from_filename_none_when_no_suffix()
    print("PASS: all generate_software_catalog tests")
