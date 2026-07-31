# -*- coding: utf-8 -*-
"""NoteForge feishu_sync unit tests.

Run:
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_feishu_sync.py -v
"""
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch
import pytest

def _import_fs():
    return __import__("noteforge.integration.feishu_sync", fromlist=["feishu_sync"])


def _fs():
    return _import_fs()


# ========== Helpers ==========

def _mk_note(path, title, content="# Test\n\nBody.\n"):
    p = path / title
    p.write_text(content, encoding="utf-8")
    return p


class MockClient:
    """Deterministic FeishuClient mock — same (parent, name) always returns same token."""

    def __init__(self):
        self.nodes = {}
        self.docs = {}
        self._by_title = {}

    def _hash_tok(self, parent, name):
        import hashlib
        return "tok_" + hashlib.md5(f"{parent}:{name}".encode()).hexdigest()[:6]

    def ensure_category_node(self, parent, name):
        tok = self._hash_tok(parent, name)
        if tok not in self.nodes:
            node = {"node_token": tok, "title": name, "parent_token": parent}
            self.nodes[f"{tok}:{name}"] = node
            self._by_title.setdefault(parent, {})[name] = node
        return tok

    def find_node_by_title(self, tok, title):
        return self._by_title.get(tok, {}).get(title)

    def overwrite_document(self, doc_token, blocks):
        self.docs[doc_token] = blocks

    def create_document_and_write(self, parent, title, blocks):
        tok = "doc_" + title.replace(" ", "_").replace(".", "_")[:20]
        self.docs[tok] = blocks
        return tok

    def list_child_nodes(self, tok):
        return [v for v in self.nodes.values() if v.get("parent_token") == tok]


_CAT = "课程笔记"
_OTHER = "其他笔记"


def _feishu_config():
    return {
        "feishu": {
            "enabled": True, "space_id": "s", "root_node_token": "r",
            "categories": [
                {"name": _CAT, "match": ["lec*", "ep*"]},
                {"name": "金融投资", "match": ["*quant*", "*fund*"]},
                {"name": _OTHER, "match": ["*"]},
            ],
            "exclude_patterns": ["*draft*"],
        }
    }


# ========== TestContentHash ==========

class TestContentHash:

    def test_deterministic(self):
        assert _fs()._content_hash("abc") == _fs()._content_hash("abc")

    def test_length_12(self):
        assert len(_fs()._content_hash("x")) == 12

    def test_different_inputs(self):
        assert _fs()._content_hash("a") != _fs()._content_hash("b")

    def test_matches_md5_prefix(self):
        c = "test"
        assert _fs()._content_hash(c) == hashlib.md5(c.encode()).hexdigest()[:12]

    def test_unicode(self):
        assert len(_fs()._content_hash("中文")) == 12


# ========== TestHashCache ==========

class TestLoadHashCache:

    def test_missing_file(self, tmp_path):
        f = tmp_path / ".nope.json"
        with patch.object(_fs(), "HASH_CACHE_FILE", f):
            assert _fs()._load_hash_cache() == {}

    def test_valid_json(self, tmp_path):
        f = tmp_path / ".c.json"
        f.write_text(json.dumps({"k": "v"}), encoding="utf-8")
        with patch.object(_fs(), "HASH_CACHE_FILE", f):
            assert _fs()._load_hash_cache() == {"k": "v"}


class TestSaveHashCache:

    def test_writes_json(self, tmp_path):
        f = tmp_path / ".c.json"
        with patch.object(_fs(), "HASH_CACHE_FILE", f):
            _fs()._save_hash_cache({"a": "1"})
        assert json.loads(f.read_text()) == {"a": "1"}

    def test_creates_parent_dirs(self, tmp_path):
        f = tmp_path / "a" / "b" / ".c.json"
        with patch.object(_fs(), "HASH_CACHE_FILE", f):
            _fs()._save_hash_cache({"x": "y"})
        assert f.exists()


# ========== TestEnvFile ==========

class TestLoadEnvFile:

    def test_sets_vars(self, tmp_path):
        (tmp_path / ".env").write_text("A=hello\n", encoding="utf-8")
        fs = _fs(); orig = fs.PROJECT_ROOT
        try:
            fs.PROJECT_ROOT = tmp_path
            os.environ.pop("A", None)
            fs._load_env_file()
            assert os.environ["A"] == "hello"
        finally:
            os.environ.pop("A", None); fs.PROJECT_ROOT = orig

    def test_skips_comments(self, tmp_path):
        (tmp_path / ".env").write_text("# cmt\nA=1\n", encoding="utf-8")
        fs = _fs(); orig = fs.PROJECT_ROOT
        try:
            fs.PROJECT_ROOT = tmp_path
            os.environ.pop("A", None)
            fs._load_env_file()
            assert os.environ["A"] == "1"
        finally:
            os.environ.pop("A", None); fs.PROJECT_ROOT = orig

    def test_missing_file_no_error(self, tmp_path):
        fs = _fs(); orig = fs.PROJECT_ROOT
        try:
            fs.PROJECT_ROOT = tmp_path / "nonexistent"
            fs._load_env_file()
        finally:
            fs.PROJECT_ROOT = orig


# ========== TestScanNotes ==========

def _cfg():
    return {
        "feishu": {
            "enabled": True, "space_id": "s", "root_node_token": "r",
            "categories": [
                {"name": _CAT, "match": ["lec*", "ep*"]},
                {"name": _OTHER, "match": ["*"]},
            ],
            "exclude_patterns": ["*draft*"],
        }
    }


class TestScanNotes:

    def test_returns_tuple(self):
        fs = _fs()
        with patch.object(fs, "_load_config",
                          return_value={"feishu": {"categories": [], "exclude_patterns": []}}):
            with patch.object(fs, "BASE_DIR", Path("/tmp")):
                r = fs.scan_notes()
        assert isinstance(r, tuple) and len(r) == 2

    def test_empty_dir(self, tmp_path):
        (tmp_path / "output" / "notes").mkdir(parents=True)
        with patch.object(_fs(), "_load_config", return_value=_cfg()):
            with patch.object(_fs(), "BASE_DIR", tmp_path):
                g, m = _fs().scan_notes()
        assert g == {} and m == set()

    def test_new_format_categorization(self, tmp_path):
        d = tmp_path / "output" / "notes"; d.mkdir(parents=True)
        _mk_note(d, "lec01.md"); _mk_note(d, "lec02.md")
        _mk_note(d, "quant_note.md"); _mk_note(d, "fund_guide.md")
        _mk_note(d, "misc.md"); _mk_note(d, "draft_skip.md")

        with patch.object(_fs(), "_load_config", return_value=_feishu_config()):
            with patch.object(_fs(), "BASE_DIR", tmp_path):
                g, m = _fs().scan_notes()

        paths = " ".join(g.keys())
        assert _CAT in paths
        assert "金融投资" in paths
        assert _OTHER in paths
        assert "draft_skip.md" not in m
        assert "misc.md" in m

    def test_exclude_patterns(self, tmp_path):
        d = tmp_path / "output" / "notes"; d.mkdir(parents=True)
        _mk_note(d, "note.md"); _mk_note(d, "draft_v1.md")

        with patch.object(_fs(), "_load_config", return_value=_cfg()):
            with patch.object(_fs(), "BASE_DIR", tmp_path):
                _, m = _fs().scan_notes()
        assert "draft_v1.md" not in m
        assert "note.md" in m

    def test_other_notes_is_flat(self, tmp_path):
        d = tmp_path / "output" / "notes"; d.mkdir(parents=True)
        _mk_note(d, "random.md"); _mk_note(d, "misc.md")

        with patch.object(_fs(), "_load_config", return_value=_cfg()):
            with patch.object(_fs(), "BASE_DIR", tmp_path):
                g, _ = _fs().scan_notes()
        for p in g:
            assert not p.endswith("/跨集提炼"), f"unexpected sub-node: {p}"
            assert not p.endswith("/逐集笔记"), f"unexpected sub-node: {p}"

    def test_regular_has_sub_nodes(self, tmp_path):
        d = tmp_path / "output" / "notes"; d.mkdir(parents=True)
        _mk_note(d, "lec01_知识体系.md")
        _mk_note(d, "lec02_普通.md")

        with patch.object(_fs(), "_load_config", return_value=_cfg()):
            with patch.object(_fs(), "BASE_DIR", tmp_path):
                g, _ = _fs().scan_notes()
        assert any("跨集提炼" in k for k in g), f"keys: {list(g.keys())}"
        assert any("逐集笔记" in k for k in g), f"keys: {list(g.keys())}"

    def test_synthesis_keywords_route_to_cross_refine(self, tmp_path):
        d = tmp_path / "output" / "notes"; d.mkdir(parents=True)
        _mk_note(d, "lec01_知识体系.md")
        _mk_note(d, "lec02_模型分析.md")
        _mk_note(d, "lec03_普通episode.md")

        with patch.object(_fs(), "_load_config", return_value=_cfg()):
            with patch.object(_fs(), "BASE_DIR", tmp_path):
                g, _ = _fs().scan_notes()

        cross = [k for k in g if "跨集提炼" in k]
        assert len(cross) > 0, f"keys: {list(g.keys())}"
        for path_key, files in g.items():
            if "跨集提炼" in path_key:
                fnames = [f[0] for f in files]
                assert any("知识体系" in f or "模型" in f for f in fnames), f"files: {fnames}"

    def test_first_match_wins(self, tmp_path):
        d = tmp_path / "output" / "notes"; d.mkdir(parents=True)
        _mk_note(d, "lec01_test.md")
        cfg = {
            "feishu": {
                "enabled": True, "space_id": "s", "root_node_token": "r",
                "categories": [
                    {"name": _CAT, "match": ["lec*"]},
                    {"name": _OTHER, "match": ["*"]},
                ],
                "exclude_patterns": [],
            }
        }
        with patch.object(_fs(), "_load_config", return_value=cfg):
            with patch.object(_fs(), "BASE_DIR", tmp_path):
                _, m = _fs().scan_notes()
        assert "lec01_test.md" in m


# ========== TestSyncNode ==========

# sub-node names match code internals
_SEQ = "逐集笔记"
_CROSS = "跨集提炼"


class TestSyncNode:

    def _make_groups(self, tmp_path):
        d = tmp_path / "output" / "notes"; d.mkdir(parents=True)
        n1 = _mk_note(d, "lec01", "# Ep1\n")
        n2 = _mk_note(d, "lec02", "# Ep2\n")
        return {
            f"{_CAT}/{_SEQ}": [("lec01", n1), ("lec02", n2)],
        }

    def _cfg(self):
        return {"name": _CAT, "match": ["lec*"]}

    def _run(self, groups, client, items, cache, flt=None, new_only=False):
        fs = _fs()
        with patch.object(fs, "md_to_blocks", return_value=[{"type": "text"}]), \
             patch.object(fs, "_renumber_category", return_value=None):
            return fs._sync_node(
                client=client, node_config=self._cfg(),
                parent_node_token="root", groups=groups,
                path=_CAT,
                file_filter=flt, new_only=new_only, dry_run=False,
                sync_items=items, hash_cache=cache,
            )

    def test_new_created(self, tmp_path):
        g = self._make_groups(tmp_path)
        c = MockClient(); items = []; hc = {}
        s, sk, e = self._run(g, c, items, hc)
        assert s == 2 and sk == 0 and e == 0
        assert len(items) == 2
        assert all(i.action == "created" for i in items)

    def test_new_only_skips(self, tmp_path):
        g = self._make_groups(tmp_path)
        c = MockClient(); items = []; hc = {}
        cat = c.ensure_category_node("root", _CAT)
        seq = c.ensure_category_node(cat, _SEQ)
        # _sync_node uses clean title (no prefix) for lookup
        c._by_title.setdefault(seq, {})["lec01"] = {
            "node_token": seq, "title": "lec01", "obj_token": "d1"
        }
        s, sk, e = self._run(g, c, items, hc, new_only=True)
        # lec01 skipped (exists), lec02 created (new)
        assert s == 1 and sk == 1 and e == 0
        skp = [i for i in items if i.action == "skipped"]
        assert len(skp) == 1

    def test_hash_cache_skips(self, tmp_path):
        g = self._make_groups(tmp_path)
        c = MockClient(); items = []; hc = {}
        cat = c.ensure_category_node("root", _CAT)
        seq = c.ensure_category_node(cat, _SEQ)
        c._by_title.setdefault(seq, {})["lec01"] = {
            "node_token": seq, "title": "lec01", "obj_token": "d1"
        }
        content_text = g[f"{_CAT}/{_SEQ}"][0][1].read_text(encoding="utf-8")
        hc_key = f"{_CAT}/lec01"
        hc[hc_key] = _fs()._content_hash(content_text)
        s, sk, e = self._run(g, c, items, hc, new_only=False)
        assert s == 1 and sk == 1 and e == 0

    def test_content_changed_updates(self, tmp_path):
        g = self._make_groups(tmp_path)
        c = MockClient(); items = []; hc = {}
        cat = c.ensure_category_node("root", _CAT)
        seq = c.ensure_category_node(cat, _SEQ)
        c._by_title.setdefault(seq, {})["lec01"] = {
            "node_token": seq, "title": "lec01", "obj_token": "d1"
        }
        hc_key = f"{_CAT}/lec01"
        hc[hc_key] = "wronghash"  # mismatch → triggers update
        s, sk, e = self._run(g, c, items, hc)
        # lec01 updated (hash mismatch), lec02 created (new)
        assert s == 2 and sk == 0 and e == 0
        upd = [i for i in items if i.action == "updated"]
        assert len(upd) == 1
        crt = [i for i in items if i.action == "created"]
        assert len(crt) == 1

    def test_file_read_error(self, tmp_path):
        d = tmp_path / "output" / "notes"; d.mkdir(parents=True)
        n1 = _mk_note(d, "lec01", "# Ep1\n")
        n2 = _mk_note(d, "lec02", "# Ep2\n")
        n2.unlink()  # make read fail
        groups = {f"{_CAT}/{_SEQ}": [("lec01", n1), ("lec02", n2)]}
        c = MockClient(); items = []; hc = {}
        fs = _fs()
        with patch.object(fs, "md_to_blocks", return_value=[{"type": "text"}]):
            s, sk, e = fs._sync_node(
                client=c, node_config=self._cfg(),
                parent_node_token="root", groups=groups,
                path=_CAT,
                file_filter=None, new_only=False, dry_run=False,
                sync_items=items, hash_cache=hc,
            )
        assert s == 1 and e == 1

    def test_file_filter(self, tmp_path):
        d = tmp_path / "output" / "notes"; d.mkdir(parents=True)
        n1 = _mk_note(d, "lec01", "# Ep1\n")
        n2 = _mk_note(d, "lec02", "# Ep2\n")
        groups = {f"{_CAT}/{_SEQ}": [("lec01", n1), ("lec02", n2)]}
        c = MockClient(); items = []; hc = {}
        s, sk, e = self._run(groups, c, items, hc, flt="lec01")
        assert s == 1 and sk == 0 and e == 0

    def test_v5_suffix_stripped(self, tmp_path):
        d = tmp_path / "output" / "notes"; d.mkdir(parents=True)
        n1 = _mk_note(d, "lec01_v5.md", "# Ep1\n")
        groups = {f"{_CAT}/{_SEQ}": [("lec01_v5.md", n1)]}
        c = MockClient(); items = []; hc = {}
        s, sk, e = self._run(groups, c, items, hc)
        assert s == 1
        # _sync_node strips "_v5" and uses clean title (no auto-prefix; renumber handles that)
        assert items[0].title == "lec01"

    def test_items_recorded(self, tmp_path):
        g = self._make_groups(tmp_path)
        c = MockClient(); items = []; hc = {}
        s, sk, e = self._run(g, c, items, hc)
        assert len(items) == 2
        SyncItem = _fs().SyncItem
        assert all(isinstance(i, SyncItem) for i in items)
        assert all(i.category == _CAT for i in items)
