"""Experiment I/O helpers: corpus file digest + JSONL validation (EXPERIMENTS.md §5)."""

from __future__ import annotations

import hashlib

import pytest

from app.experiments.io import CorpusFileError, file_sha256, read_corpus_jsonl


def test_file_sha256_matches_hashlib_over_exact_bytes(tmp_path) -> None:
    payload = b'{"a": 1}\n\n{"b": "caf\xc3\xa9"}\n'
    path = tmp_path / "corpus.jsonl"
    path.write_bytes(payload)

    assert file_sha256(path) == hashlib.sha256(payload).hexdigest()


def test_file_sha256_streams_large_files(tmp_path) -> None:
    # >1 MiB forces the chunked read path to iterate.
    payload = b'{"x": "' + b"y" * (1 << 20) + b'"}\n'
    path = tmp_path / "big.jsonl"
    path.write_bytes(payload)

    assert file_sha256(path) == hashlib.sha256(payload).hexdigest()


def test_read_corpus_jsonl_skips_blank_lines(tmp_path) -> None:
    path = tmp_path / "corpus.jsonl"
    path.write_text('{"a": 1}\n\n   \n{"b": 2}\n', encoding="utf-8")

    assert read_corpus_jsonl(path) == [{"a": 1}, {"b": 2}]


def test_read_corpus_jsonl_reports_line_number(tmp_path) -> None:
    path = tmp_path / "corpus.jsonl"
    path.write_text('{"a": 1}\nnot json\n', encoding="utf-8")

    with pytest.raises(CorpusFileError, match=r":2: invalid JSON"):
        read_corpus_jsonl(path)
