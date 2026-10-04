"""Evaluation regression tests: the labelled datasets must keep passing."""

from __future__ import annotations

import json

import pytest

from app.evaluation.corpus import FIXTURES, manifest
from app.evaluation.proposal_eval import run_offline


def test_proposal_dataset_label_agreement():
    report = run_offline()
    assert report["disagreements"] == []
    assert report["claim_block_rate"] == 1.0
    assert report["grounded_false_rejection_rate"] == 0.0


def test_corpus_manifest_is_complete_and_serialisable():
    data = manifest()
    json.dumps(data)
    features = {f for fx in data["fixtures"] for f in fx["features"]}
    required = {
        "one_page",
        "multi_page",
        "run_formatting",
        "multi_run_text",
        "bullets",
        "nested_lists",
        "tables",
        "nested_tables",
        "columns",
        "sections",
        "headers",
        "footers",
        "hyperlinks",
        "images",
        "fonts",
        "spacing",
        "fields",
        "text_boxes",
        "tracked_changes",
    }
    assert required <= features
    assert all(fx["edits"] for fx in data["fixtures"])


def test_corpus_is_deterministic():
    for fx in FIXTURES:
        assert fx.build() == fx.build(), fx.fixture_id


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.fixture_id)
def test_read_only_texts_are_not_editable(fixture):
    from app.document.docx_parser import parse_docx

    index, _ = parse_docx(fixture.build())
    for text in fixture.read_only_texts:
        blocks = [b for b in index.blocks if text in b.text]
        assert blocks, text
        # Either the whole paragraph is read-only or the text sits inside a read-only/field segment.
        from app.document.docx_parser import DocxPackage
        from app.document.location_index import LocationResolver

        resolver = LocationResolver(DocxPackage(fixture.build()))
        for blk in blocks:
            model = resolver.paragraph(blk.part, blk.path)
            segs = [s for s in model.segments if s.kind.value == "text" and text in s.text]
            assert not blk.editable or all(not s.editable for s in segs), (fixture.fixture_id, text)
