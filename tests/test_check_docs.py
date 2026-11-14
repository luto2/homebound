"""tools/check_docs.py: dead links, cited doc paths and the header block."""
import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("check_docs", Path(__file__).resolve().parent.parent / "tools" / "check_docs.py")
check_docs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_docs)

HEADER = "# T\n\n- **Audience:** a.\n- **Scope:** b.\n- **Kept current:** c.\n"


def test_a_link_to_a_missing_file_is_reported_and_a_link_to_an_existing_one_is_not() -> None:
    problems = check_docs.check_links("docs/README.md", "[ok](architecture.md) [bad](no-such-doc.md) [web](https://example.org) [here](#x)")
    assert problems == ["docs/README.md: dead link no-such-doc.md"]


def test_links_inside_code_are_not_checked() -> None:
    assert check_docs.check_links("docs/README.md", "`[a](nope.md)`\n```\n[b](nope.md)\n```") == []


def test_a_cited_doc_path_must_exist() -> None:
    problems = check_docs.check_cited_paths("homebound/x.py", "# see docs/plans/missing-plan.md and docs/architecture.md")
    assert problems == ["homebound/x.py: cited path does not exist: docs/plans/missing-plan.md"]


def test_the_header_block_needs_all_three_fields() -> None:
    assert check_docs.check_header("a.md", HEADER) == []
    assert check_docs.check_header("a.md", "# T\n\n- **Audience:** a.\n") == [
        "a.md: header block lacks **Scope:**, **Kept current:**"]


def test_design_system_and_private_docs_need_no_header() -> None:
    assert not check_docs.is_doc_needing_header("design-system/README.md")
    assert not check_docs.is_doc_needing_header("docs/private/operations.md")
    assert check_docs.is_doc_needing_header("docs/code-map.md")
