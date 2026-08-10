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

def _mk_note(path, title, content=None):
    p = path / title
    if content is None:
        # B3: can_sync() 验证要求内容足够长（≥100 字实质内容 + ≥1 个二级标题 + ≥3 行实质内容）
        content = (
            "# Test\n\n"
            "## 核心观点\n\n"
            "这是测试笔记的核心观点，包含足够的实质内容来通过同步验证。\n"
            "第二行内容确保长度和结构都满足要求，不会因为过短而被阻止。\n"
            "第三行内容确保实质内容行数足够，同时确保总字符数超过一百字。\n"
            "第四行额外的内容保证去掉标题和元数据后仍有足够实质内容。\n\n"
        )
    p.write_text(content, encoding="utf-8")
    return p


# B3: can_sync() 验证通过的测试内容（供需要显式指定 content 的测试用）
_VALID_NOTE_CONTENT = (
    "# Ep1\n\n"
    "## 核心观点\n\n"
    "这是测试笔记的核心观点，包含足够的实质内容来通过同步验证。\n"
    "第二行内容确保长度和结构都满足要求，不会因为过短而被阻止。\n"
    "第三行内容确保实质内容行数足够，同时确保总字符数超过一百字。\n"
    "第四行额外的内容保证去掉标题和元数据后仍有足够实质内容。\n\n"
)


class MockClient:
    """Deterministic FeishuClient mock — same (parent, name) always returns same token."""

    def __init__(self):
        self.nodes = {}
        self.docs = {}
        self._by_title = {}
        self.space_id = "test_space"
        self.dry_run = False

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
        # 合并两个数据源，保持与 find_node_by_title 一致（真实 FeishuClient 中两者同源）：
        # 1. ensure_category_node 注册的分类节点（带 parent_token）
        # 2. 测试手动 seed 到 _by_title 的文档节点
        seen: set = set()
        result = []
        for v in self.nodes.values():
            if v.get("parent_token") == tok:
                result.append(v)
                seen.add(v.get("node_token"))
        for title, node in self._by_title.get(tok, {}).items():
            nt = node.get("node_token")
            if nt and nt not in seen:
                result.append(node)
                seen.add(nt)
        return result


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

    def test_junk_patterns_skipped(self, tmp_path):
        """junk_patterns 文件名模式应跳过（招生简章/上线通知等无知识内容）"""
        d = tmp_path / "output" / "notes"; d.mkdir(parents=True)
        _mk_note(d, "正常笔记.md")
        _mk_note(d, "2024年招生简章.md")
        _mk_note(d, "课程上线通知.md")
        cfg = {
            "feishu": {
                "enabled": True, "space_id": "s", "root_node_token": "r",
                "categories": [{"name": _OTHER, "match": ["*"]}],
                "exclude_patterns": [],
                "junk_patterns": ["*简章*", "*招生*", "*上线通知*"],
            }
        }
        with patch.object(_fs(), "_load_config", return_value=cfg):
            with patch.object(_fs(), "BASE_DIR", tmp_path):
                _, m = _fs().scan_notes()
        assert "正常笔记.md" in m
        assert "2024年招生简章.md" not in m
        assert "课程上线通知.md" not in m

    def test_low_value_content_skipped(self, tmp_path):
        """内容自述无知识可提炼（如人大招生简章）应跳过，即使文件名无标记"""
        d = tmp_path / "output" / "notes"; d.mkdir(parents=True)
        _mk_note(d, "正常笔记.md")
        junk = d / "普通标题.md"
        junk.write_text(
            "基于当前文本，无法生成符合用户要求（提取分析方法）的结构化学习笔记。",
            encoding="utf-8",
        )
        cfg = {
            "feishu": {
                "enabled": True, "space_id": "s", "root_node_token": "r",
                "categories": [{"name": _OTHER, "match": ["*"]}],
                "exclude_patterns": [],
                "junk_patterns": [],
            }
        }
        with patch.object(_fs(), "_load_config", return_value=cfg):
            with patch.object(_fs(), "BASE_DIR", tmp_path):
                _, m = _fs().scan_notes()
        assert "正常笔记.md" in m
        assert "普通标题.md" not in m

    def test_junk_patterns_default_empty(self, tmp_path):
        """无 junk_patterns 配置时不误杀（默认空列表）"""
        d = tmp_path / "output" / "notes"; d.mkdir(parents=True)
        _mk_note(d, "正常笔记.md")
        with patch.object(_fs(), "_load_config", return_value=_cfg()):
            with patch.object(_fs(), "BASE_DIR", tmp_path):
                _, m = _fs().scan_notes()
        assert "正常笔记.md" in m

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
        """仅「知识体系/跨集/提炼」标记进跨集提炼；含「模型/框架」的逐集笔记不混入"""
        d = tmp_path / "output" / "notes"; d.mkdir(parents=True)
        _mk_note(d, "lec01_知识体系.md")
        _mk_note(d, "lec02_模型分析.md")
        _mk_note(d, "lec03_框架解读.md")
        _mk_note(d, "lec04_普通episode.md")

        with patch.object(_fs(), "_load_config", return_value=_cfg()):
            with patch.object(_fs(), "BASE_DIR", tmp_path):
                g, _ = _fs().scan_notes()

        cross_paths = [k for k in g if "跨集提炼" in k]
        ep_paths = [k for k in g if "逐集笔记" in k]
        assert len(cross_paths) == 1, f"keys: {list(g.keys())}"
        cross_files = [f[0] for f in g[cross_paths[0]]]
        assert "lec01_知识体系.md" in cross_files, f"files: {cross_files}"

        ep_files = [f[0] for p in ep_paths for f in g[p]]
        # 模型/框架/普通笔记 → 逐集笔记，不进跨集提炼
        assert "lec02_模型分析.md" in ep_files, f"模型笔记被错误分入跨集提炼: {ep_files}"
        assert "lec03_框架解读.md" in ep_files, f"框架笔记被错误分入跨集提炼: {ep_files}"
        assert "lec04_普通episode.md" in ep_files
        assert all("模型" not in f and "框架" not in f for f in cross_files)

    def test_ai_interview_notes_not_mixed_into_cross_refine(self, tmp_path):
        """回归：AI 访谈笔记标题含「模型/框架」，不得混入跨集提炼（2026-08-03 修复）"""
        d = tmp_path / "output" / "notes"; d.mkdir(parents=True)
        # 真实 AI 域文件名（访谈标题都含"模型"）
        _mk_note(d, "全球大模型第一股的上市访谈，和智谱CEO张鹏聊：敢问路在何方？.md")
        _mk_note(d, "对姚顺宇的4小时访谈：在Anthropic和Gemini训模型.md")
        _mk_note(d, "对罗福莉的3.5小时访谈：OpenClaw、智能体框架、Agent范式.md")
        _mk_note(d, "AI与大模型-知识体系.md")  # 真正的合成文档

        cfg = {
            "feishu": {
                "enabled": True, "space_id": "s", "root_node_token": "r",
                "categories": [
                    {"name": "AI与大模型", "match": ["*访谈*", "*大模型*", "*Anthropic*"],
                     "exclude": ["*量化*", "*地缘*"]},
                    {"name": "其他笔记", "match": ["*"]},
                ],
                "exclude_patterns": [],
            }
        }
        with patch.object(_fs(), "_load_config", return_value=cfg):
            with patch.object(_fs(), "BASE_DIR", tmp_path):
                g, _ = _fs().scan_notes()

        cross_files = [f[0] for k, files in g.items() if "跨集提炼" in k for f in files]
        ep_files = [f[0] for k, files in g.items() if "逐集笔记" in k for f in files]

        # 跨集提炼只能有知识体系文档（唯一）
        assert cross_files == ["AI与大模型-知识体系.md"], f"cross: {cross_files}"
        # 3 篇访谈全部在逐集笔记
        for name in cross_files:
            pass
        assert len(ep_files) == 3, f"ep: {ep_files}"
        assert all("访谈" in f for f in ep_files)

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

    def test_exclude_prevents_cross_domain_hijack(self, tmp_path):
        """exclude 模式应阻止跨域截胡：含排除关键词的文件跳过当前分类"""
        d = tmp_path / "output" / "notes"; d.mkdir(parents=True)
        # 翟东升相关文件应归入地缘政治，不被短视频截胡
        _mk_note(d, "翟东升-中美博弈.md")
        _mk_note(d, "导演技巧.md")
        _mk_note(d, "misc.md")

        cfg = {
            "feishu": {
                "enabled": True, "space_id": "s", "root_node_token": "r",
                "categories": [
                    {"name": "地缘政治", "match": ["*翟东升*", "*中美*", "*博弈*"],
                     "exclude": ["*导演*", "*短视频*"]},
                    {"name": "短视频导演课程", "match": ["*导演*"],
                     "exclude": ["*翟东升*", "*中美*", "*博弈*"]},
                    {"name": "其他笔记", "match": ["*"]},
                ],
                "exclude_patterns": [],
            }
        }
        with patch.object(_fs(), "_load_config", return_value=cfg):
            with patch.object(_fs(), "BASE_DIR", tmp_path):
                g, m = _fs().scan_notes()

        # 翟东升文件应归入地缘政治，不是短视频
        geo_files = [f[0] for files in g.values() for f in files
                     if any("地缘政治" in k for k in g if f in g[k])]
        geo_paths = [k for k in g if "地缘政治" in k]
        assert len(geo_paths) > 0, f"翟东升文件未归入地缘政治, groups: {list(g.keys())}"
        geo_filenames = []
        for p in geo_paths:
            geo_filenames.extend(f[0] for f in g[p])
        assert "翟东升-中美博弈.md" in geo_filenames

        # 导演文件应归入短视频
        sv_paths = [k for k in g if "短视频" in k]
        assert len(sv_paths) > 0
        sv_filenames = []
        for p in sv_paths:
            sv_filenames.extend(f[0] for f in g[p])
        assert "导演技巧.md" in sv_filenames

    def test_knowledge_system_file_routed_correctly(self, tmp_path):
        """知识体系文件应按分类名前缀正确归类，不被通用词截胡"""
        d = tmp_path / "output" / "notes"; d.mkdir(parents=True)
        _mk_note(d, "地缘经济-知识体系.md")
        _mk_note(d, "量化投资-知识体系.md")
        _mk_note(d, "地缘政治-知识体系.md")

        cfg = {
            "feishu": {
                "enabled": True, "space_id": "s", "root_node_token": "r",
                "categories": [
                    {"name": "地缘政治", "match": ["*地缘政治*"],
                     "exclude": ["*导演*"]},
                    {"name": "地缘经济", "match": ["*地缘经济*"],
                     "exclude": ["*翟东升*"]},
                    {"name": "量化投资", "match": ["*量化*"],
                     "exclude": ["*翟东升*", "*地缘*"]},
                    {"name": "其他笔记", "match": ["*"]},
                ],
                "exclude_patterns": [],
            }
        }
        with patch.object(_fs(), "_load_config", return_value=cfg):
            with patch.object(_fs(), "BASE_DIR", tmp_path):
                g, m = _fs().scan_notes()

        # 地缘经济-知识体系 → 地缘经济/跨集提炼（不是量化投资）
        geo_econ_cross = [k for k in g if "地缘经济" in k and "跨集提炼" in k]
        assert len(geo_econ_cross) > 0, f"地缘经济-知识体系未归入地缘经济/跨集提炼, keys: {list(g.keys())}"
        geo_econ_files = [f[0] for f in g[geo_econ_cross[0]]]
        assert "地缘经济-知识体系.md" in geo_econ_files

        # 量化投资-知识体系 → 量化投资/跨集提炼
        quant_cross = [k for k in g if "量化投资" in k and "跨集提炼" in k]
        assert len(quant_cross) > 0, f"量化投资-知识体系未归入量化投资/跨集提炼, keys: {list(g.keys())}"

    def test_broad_keyword_excluded_by_specific(self, tmp_path):
        """通用关键词（如'经济'）不应截胡有更具体标识的文件"""
        d = tmp_path / "output" / "notes"; d.mkdir(parents=True)
        # 政经启翟文件含"经济"但应归地缘政治
        _mk_note(d, "【政经启翟】经济博弈分析.md")
        _mk_note(d, "量化基金策略.md")

        cfg = {
            "feishu": {
                "enabled": True, "space_id": "s", "root_node_token": "r",
                "categories": [
                    {"name": "地缘政治", "match": ["*政经启翟*", "*翟东升*"],
                     "exclude": ["*导演*"]},
                    {"name": "量化投资", "match": ["*量化*", "*基金*"],
                     "exclude": ["*翟东升*", "*政经启翟*"]},
                    {"name": "其他笔记", "match": ["*"]},
                ],
                "exclude_patterns": [],
            }
        }
        with patch.object(_fs(), "_load_config", return_value=cfg):
            with patch.object(_fs(), "BASE_DIR", tmp_path):
                g, m = _fs().scan_notes()

        # 政经启翟文件 → 地缘政治
        geo_paths = [k for k in g if "地缘政治" in k]
        geo_files = []
        for p in geo_paths:
            geo_files.extend(f[0] for f in g[p])
        assert "【政经启翟】经济博弈分析.md" in geo_files

        # 量化基金 → 量化投资
        quant_paths = [k for k in g if "量化投资" in k]
        quant_files = []
        for p in quant_paths:
            quant_files.extend(f[0] for f in g[p])
        assert "量化基金策略.md" in quant_files


# ========== TestAutoPromote ==========

# 知识域配置（测试用，与 detect_pending_categories 的输入格式一致）
def _domains():
    return [
        {"id": "finance_investment", "name": "量化投资",
         "match_files": ["*量化*", "*黄金*"],
         "match_keywords": ["量化", "基金", "黄金", "汇率", "达利欧"],
         "exclude_keywords": ["导演", "短视频"]},
        {"id": "ai_tech", "name": "AI与大模型",
         "match_files": ["*大模型*", "*Agent*"],
         "match_keywords": ["大模型", "智能体", "Agent", "访谈"],
         "exclude_keywords": ["翟东升"]},
        {"id": "general", "name": "其他", "match_keywords": [], "match_files": []},
    ]


def _other_group(tmp_path, filenames, content=None):
    """构造「其他笔记」分组（filename -> Path），返回 groups dict。"""
    d = tmp_path / "output" / "notes"; d.mkdir(parents=True)
    files = []
    for name in filenames:
        p = d / name
        p.write_text(content or _VALID_NOTE_CONTENT, encoding="utf-8")
        files.append((name, p))
    return {"其他笔记": files}


class TestDetectPending:

    def test_empty_when_no_other(self, tmp_path):
        assert _fs().detect_pending_categories({}, _domains(), tmp_path, 5) == {}

    def test_detects_cluster_at_threshold(self, tmp_path):
        names = [f"AI访谈{i}.md" for i in range(5)]
        g = _other_group(tmp_path, names)
        r = _fs().detect_pending_categories(g, _domains(), tmp_path, 5)
        assert "ai_tech" in r and len(r["ai_tech"]) == 5

    def test_below_threshold_not_detected(self, tmp_path):
        names = [f"AI访谈{i}.md" for i in range(4)]
        g = _other_group(tmp_path, names)
        r = _fs().detect_pending_categories(g, _domains(), tmp_path, 5)
        assert r == {}

    def test_uses_match_files(self, tmp_path):
        names = [f"黄金分析{i}.md" for i in range(5)]
        g = _other_group(tmp_path, names)
        r = _fs().detect_pending_categories(g, _domains(), tmp_path, 5)
        assert "finance_investment" in r and len(r["finance_investment"]) == 5

    def test_uses_content_keywords(self, tmp_path):
        # 文件名不含关键词，靠内容命中
        names = [f"演讲稿{i}.md" for i in range(5)]
        content = "# T\n\n" + "量化投资策略与基金配置的深度分析。\n" * 30
        g = _other_group(tmp_path, names, content=content)
        r = _fs().detect_pending_categories(g, _domains(), tmp_path, 5)
        assert "finance_investment" in r and len(r["finance_investment"]) == 5

    def test_respects_exclude_keywords(self, tmp_path):
        # 文件名/内容含域的排除词 → 不算该域
        names = [f"AI访谈{i}.md" for i in range(5)]
        content = "# T\n\n" + "关于翟东升与政经启翟的讨论。\n" * 30
        g = _other_group(tmp_path, names, content=content)
        r = _fs().detect_pending_categories(g, _domains(), tmp_path, 5)
        assert "ai_tech" not in r


class TestPromotePending:

    def test_skips_existing_category(self, tmp_path):
        names = [f"AI访谈{i}.md" for i in range(5)]
        g = _other_group(tmp_path, names)
        categories = [{"name": "AI与大模型", "match": ["*访谈*"]}]
        r = _fs().promote_pending_categories(
            MockClient(), g, {"ai_tech": names}, _domains(), categories,
            "root", None, False, False, [], {})
        assert r[0] == 0  # promoted = 0（已存在配置分类）
        # 文件不应被移动
        assert len(g.get("其他笔记", [])) == 5

    def test_creates_new_category(self, tmp_path):
        names = [f"AI访谈{i}.md" for i in range(5)]
        g = _other_group(tmp_path, names)
        categories = [{"name": "其他笔记", "match": ["*"]}]
        client = MockClient()
        with patch.object(_fs(), "md_to_blocks", return_value=[{"type": "text"}]), \
             patch.object(_fs(), "_persist_category_config", return_value=True):
            r = _fs().promote_pending_categories(
                client, g, {"ai_tech": names}, _domains(), categories,
                "root", None, False, False, [], {})
        assert r[0] == 1  # promoted = 1
        assert len(g.get("AI与大模型/逐集笔记", [])) == 5
        assert g.get("其他笔记", []) == []

    def test_dry_run_no_mutation(self, tmp_path):
        names = [f"AI访谈{i}.md" for i in range(5)]
        g = _other_group(tmp_path, names)
        categories = [{"name": "其他笔记", "match": ["*"]}]
        with patch.object(_fs(), "_persist_category_config") as persist:
            r = _fs().promote_pending_categories(
                MockClient(), g, {"ai_tech": names}, _domains(), categories,
                "root", None, False, True, [], {})
        assert r[0] == 1
        assert g.get("AI与大模型/逐集笔记", None) is None  # 未移动
        assert len(g.get("其他笔记", [])) == 5
        persist.assert_not_called()

    def test_routes_synthesis_file_to_cross_refine(self, tmp_path):
        # 知识体系类文件名应归入跨集提炼
        names = [f"AI访谈{i}.md" for i in range(4)] + ["AI知识体系.md"]
        g = _other_group(tmp_path, names)
        categories = [{"name": "其他笔记", "match": ["*"]}]
        with patch.object(_fs(), "md_to_blocks", return_value=[{"type": "text"}]), \
             patch.object(_fs(), "_persist_category_config", return_value=True):
            _fs().promote_pending_categories(
                MockClient(), g, {"ai_tech": names}, _domains(), categories,
                "root", None, False, False, [], {})
        assert len(g.get("AI与大模型/跨集提炼", [])) == 1
        assert g["AI与大模型/跨集提炼"][0][0] == "AI知识体系.md"
        assert len(g.get("AI与大模型/逐集笔记", [])) == 4


class TestPersistCategory:

    def _write_cfg(self, tmp_path, cats_text):
        content = "# header\nfeishu:\n  categories:\n" + cats_text
        p = tmp_path / "llm_engine_config.yaml"
        p.write_text(content, encoding="utf-8")
        return p

    def test_inserts_before_other(self, tmp_path):
        cats = (
            '    - name: "AI与大模型"\n'
            '      match: ["*访谈*"]\n'
            '    - name: "其他笔记"\n'
            '      match: ["*"]\n'
        )
        p = self._write_cfg(tmp_path, cats)
        with patch.object(_fs(), "CONFIG_PATH", p):
            ok = _fs()._persist_category_config("量化投资", ["*黄金*", "*达利欧*"], ["*翟东升*"])
        assert ok
        text = p.read_text(encoding="utf-8")
        assert text.index('name: "量化投资"') < text.index('name: "其他笔记"')
        assert 'match: ["*黄金*", "*达利欧*"]' in text
        assert 'exclude: ["*翟东升*"]' in text

    def test_skips_existing_name(self, tmp_path):
        cats = (
            '    - name: "量化投资"\n'
            '      match: ["*量化*"]\n'
            '    - name: "其他笔记"\n'
            '      match: ["*"]\n'
        )
        p = self._write_cfg(tmp_path, cats)
        with patch.object(_fs(), "CONFIG_PATH", p):
            ok = _fs()._persist_category_config("量化投资", ["*黄金*"], [])
        assert not ok

    def test_missing_anchor_no_write(self, tmp_path):
        # 找不到「其他笔记」锚点 → 返回 False
        cats = '    - name: "AI与大模型"\n      match: ["*访谈*"]\n'
        p = self._write_cfg(tmp_path, cats)
        with patch.object(_fs(), "CONFIG_PATH", p):
            ok = _fs()._persist_category_config("量化投资", ["*黄金*"], [])
        assert not ok


# ========== TestSyncNode ==========

# sub-node names match code internals
_SEQ = "逐集笔记"
_CROSS = "跨集提炼"


class TestSyncNode:

    def _make_groups(self, tmp_path):
        d = tmp_path / "output" / "notes"; d.mkdir(parents=True)
        n1 = _mk_note(d, "lec01", _VALID_NOTE_CONTENT)
        n2 = _mk_note(d, "lec02", _VALID_NOTE_CONTENT)
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
        # 已有带正确序号的节点，hash 匹配 → 跳过（序号不匹配时才走重建）
        c._by_title.setdefault(seq, {})["1. lec01"] = {
            "node_token": seq, "title": "1. lec01", "obj_token": "d1"
        }
        content_text = g[f"{_CAT}/{_SEQ}"][0][1].read_text(encoding="utf-8")
        hc_key = f"{_CAT}/lec01"
        hc[hc_key] = _fs()._content_hash(content_text)
        s, sk, e = self._run(g, c, items, hc, new_only=False)
        assert s == 1 and sk == 1 and e == 0

    def test_hash_mismatch_but_wrong_number_recreates(self, tmp_path):
        """序号不匹配（内容 hash 相同也）应重建为新序号：旧格式无序号节点 → 带序号重建"""
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
        # lec01 序号不对（无序号）→ 重建为 1. lec01；lec02 新建
        assert s == 2 and sk == 0 and e == 0
        crt = [i for i in items if i.action == "created"]
        assert all(i.title.startswith("1. ") or i.title.startswith("2. ") for i in crt)

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
        # lec01 is recreated with indexed title, lec02 created (new)
        assert s == 2 and sk == 0 and e == 0
        crt = [i for i in items if i.action == "created"]
        assert len(crt) == 2
        assert all(i.title.startswith("1. ") or i.title.startswith("2. ") for i in crt)

    def test_existing_indexed_title_updates(self, tmp_path):
        """已有带序号标题的文档应正常更新内容而不重建。"""
        g = self._make_groups(tmp_path)
        c = MockClient(); items = []; hc = {}
        cat = c.ensure_category_node("root", _CAT)
        seq = c.ensure_category_node(cat, _SEQ)
        c._by_title.setdefault(seq, {})["1. lec01"] = {
            "node_token": seq, "title": "1. lec01", "obj_token": "d1"
        }
        hc_key = f"{_CAT}/lec01"
        hc[hc_key] = "wronghash"
        s, sk, e = self._run(g, c, items, hc)
        assert s == 2 and sk == 0 and e == 0
        upd = [i for i in items if i.action == "updated"]
        assert len(upd) == 1
        assert upd[0].title == "1. lec01"
        crt = [i for i in items if i.action == "created"]
        assert len(crt) == 1

    def test_file_read_error(self, tmp_path):
        d = tmp_path / "output" / "notes"; d.mkdir(parents=True)
        n1 = _mk_note(d, "lec01", _VALID_NOTE_CONTENT)
        n2 = _mk_note(d, "lec02", _VALID_NOTE_CONTENT)
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
        n1 = _mk_note(d, "lec01", _VALID_NOTE_CONTENT)
        n2 = _mk_note(d, "lec02", _VALID_NOTE_CONTENT)
        groups = {f"{_CAT}/{_SEQ}": [("lec01", n1), ("lec02", n2)]}
        c = MockClient(); items = []; hc = {}
        s, sk, e = self._run(groups, c, items, hc, flt="lec01")
        assert s == 1 and sk == 0 and e == 0

    def test_v5_suffix_stripped(self, tmp_path):
        d = tmp_path / "output" / "notes"; d.mkdir(parents=True)
        n1 = _mk_note(d, "lec01_v5.md", _VALID_NOTE_CONTENT)
        groups = {f"{_CAT}/{_SEQ}": [("lec01_v5.md", n1)]}
        c = MockClient(); items = []; hc = {}
        s, sk, e = self._run(groups, c, items, hc)
        assert s == 1
        # _sync_node strips "_v5" and adds sequence prefix for sequential notes
        assert items[0].title == "1. lec01"

    def test_items_recorded(self, tmp_path):
        g = self._make_groups(tmp_path)
        c = MockClient(); items = []; hc = {}
        s, sk, e = self._run(g, c, items, hc)
        assert len(items) == 2
        SyncItem = _fs().SyncItem
        assert all(isinstance(i, SyncItem) for i in items)
        assert all(i.category == _CAT for i in items)


# ========== TestRenumberCategory ==========

class _StubClient:
    """_renumber_category 的桩：返回预置子节点列表。"""

    def __init__(self, children):
        self.space_id = "test_space"
        self._children = children

    def list_child_nodes(self, tok):
        return self._children


class TestRenumberCategory:
    """_renumber_category 去重：删除重试 + 删除失败节点不参与重编号"""

    def _children(self):
        # "1. 相同" 和 "2. 相同" clean 后都是 "相同"（重复）；"3. 不同" 唯一
        return [
            {"node_token": "t1", "title": "1. 相同"},
            {"node_token": "t2", "title": "2. 相同"},
            {"node_token": "t3", "title": "3. 不同"},
        ]

    def _files(self):
        # _renumber_category 的 files 仅用于非空守卫
        return [Path("x.md")]

    def test_dedup_retries_failed_delete(self):
        """删除失败应重试，最终成功后剩余节点按新序重编号"""
        fs = _fs()
        client = _StubClient(self._children())
        # t2 删除第一次失败、第二次成功 → _delete_wiki_node 应被调用 2 次
        with patch.object(fs, "_delete_wiki_node", side_effect=[False, True]) as m_del, \
             patch.object(fs, "_rename_wiki_node", return_value=True) as m_ren:
            fs._renumber_category(client, "parent", self._files())
        assert m_del.call_count == 2
        # 去重后剩 t1("1. 相同"→"1. 相同")、t3("3. 不同"→"2. 不同")；仅 t3 需要重命名
        assert m_ren.call_count == 1
        m_ren.assert_called_once_with("test_space", "t3", "2. 不同")

    def test_delete_failed_node_stays_in_renumber(self):
        """删除一直失败时，重复节点应保留在重编号序列中（避免计数错位）"""
        fs = _fs()
        client = _StubClient(self._children())
        with patch.object(fs, "_delete_wiki_node", return_value=False) as m_del, \
             patch.object(fs, "_rename_wiki_node", return_value=True) as m_ren:
            fs._renumber_category(client, "parent", self._files())
        # 3 次删除尝试（每次 3 次重试）
        assert m_del.call_count == 3
        # 3 个节点都保留 → t3 编号从 3 变 3（不变），t2 保持 "2. 相同"（clean "相同" → "2. 相同" 不变）
        # t1 "1. 相同" → clean "相同" → "1. 相同" 不变 → 无重命名
        assert m_ren.call_count == 0

    def test_no_duplicate_no_delete(self):
        """无重复节点时不应调用删除"""
        fs = _fs()
        client = _StubClient([
            {"node_token": "t1", "title": "1. 甲"},
            {"node_token": "t2", "title": "2. 乙"},
        ])
        with patch.object(fs, "_delete_wiki_node", return_value=True) as m_del, \
             patch.object(fs, "_rename_wiki_node", return_value=True):
            fs._renumber_category(client, "parent", self._files())
        assert m_del.call_count == 0


# ========== P0.2: _build_child_index / _find_existing_in_index ==========

class TestChildIndex:
    """预建索引：每分类 1 次 list_child_nodes + 内存匹配，替代每篇多次调用"""

    def _children(self):
        return [
            {"node_token": "t1", "title": "1. 中美博弈"},          # 逐集笔记（带序号），clean="中美博弈"
            {"node_token": "t2", "title": "📁 跨集提炼"},           # 文件夹，clean="跨集提炼"
            {"node_token": "t3", "title": "量化投资入门"},          # 普通文档（无序号），clean="量化投资入门"
            {"node_token": "t4", "title": "3. 美联储的货币政策"},   # 逐集笔记（不同序号），clean="美联储的货币政策"
        ]

    def test_index_builds_by_title(self):
        """by_title 保留原始标题 → 节点"""
        fs = _fs()
        idx = fs._build_child_index(_StubClient(self._children()), "parent")
        assert idx["by_title"]["1. 中美博弈"]["node_token"] == "t1"
        assert idx["by_title"]["📁 跨集提炼"]["node_token"] == "t2"
        assert idx["by_title"]["量化投资入门"]["node_token"] == "t3"

    def test_index_builds_by_clean(self):
        """by_clean 用 clean_title 去 emoji/序号前缀 → 节点"""
        fs = _fs()
        idx = fs._build_child_index(_StubClient(self._children()), "parent")
        assert idx["by_clean"]["中美博弈"]["node_token"] == "t1"
        assert idx["by_clean"]["跨集提炼"]["node_token"] == "t2"
        assert idx["by_clean"]["量化投资入门"]["node_token"] == "t3"

    def test_exact_title_found(self):
        """策略 1：本地 clean title 精确命中飞书无序号节点"""
        fs = _fs()
        idx = fs._build_child_index(_StubClient(self._children()), "parent")
        node = fs._find_existing_in_index(idx, "量化投资入门", "量化投资入门", False)
        assert node["node_token"] == "t3"

    def test_display_title_with_index(self):
        """策略 2：本地 clean title 无精确匹配，但 display_title（带序号）命中飞书同序号节点"""
        fs = _fs()
        idx = fs._build_child_index(_StubClient(self._children()), "parent")
        # 飞书已有 "3. 美联储的货币政策"，本地同序号 display → 直接命中
        node = fs._find_existing_in_index(idx, "美联储的货币政策", "3. 美联储的货币政策", True)
        assert node["node_token"] == "t4"

    def test_clean_title_match(self):
        """策略 4：本地 clean title 与飞书不同序号节点 clean 相同 → 命中"""
        fs = _fs()
        idx = fs._build_child_index(_StubClient(self._children()), "parent")
        # 飞书是 "3. 美联储的货币政策"，本地要写 "2. 美联储的货币政策"
        # 精确(1)/display(2) 都未命中 → clean_title 兜底命中 t4
        node = fs._find_existing_in_index(idx, "美联储的货币政策", "2. 美联储的货币政策", True)
        assert node["node_token"] == "t4"

    def test_base_title_strip(self):
        """策略 3：入参带前缀序号、飞书有无序号节点 -> 去序号 base 命中"""
        fs = _fs()
        # 飞书侧存的是无序号 "中美博弈"，本地传入带序号 "2. 中美博弈"
        idx = fs._build_child_index(_StubClient([{"node_token": "t1", "title": "中美博弈"}]), "parent")
        node = fs._find_existing_in_index(idx, "2. 中美博弈", "2. 中美博弈", False)
        assert node["node_token"] == "t1"

    def test_not_found_returns_none(self):
        """完全不存在 → None"""
        fs = _fs()
        idx = fs._build_child_index(_StubClient(self._children()), "parent")
        assert fs._find_existing_in_index(idx, "不存在的标题", "不存在的标题", False) is None

    def test_index_tolerates_list_failure(self):
        """readonly 预览：父节点是伪 token（分类不存在）时 list 失败 → 容错为空索引

        2026-08-10: dry-run 现在也构建索引（readonly 下 GET 真实放行），
        分类不存在时拿到伪 token，list 抛异常必须降级为空索引而非崩溃——
        该分类下全部笔记显示为"将新增"，与真实行为一致。
        """
        fs = _fs()

        class _RaisingClient(_StubClient):
            def list_child_nodes(self, tok):
                raise RuntimeError(f"父节点不存在: {tok}")

        idx = fs._build_child_index(_RaisingClient(self._children()), "dry-run-new-12345")
        assert idx["children"] == []
        assert idx["by_title"] == {}
        assert idx["by_clean"] == {}


# ========== P0.1: _collect_all_titles 用 has_child 字段 ==========

class _HasChildClient:
    """记录 list_child_nodes 调用次数，验证 has_child 字段避免了逐节点递归"""

    def __init__(self):
        self.calls = 0
        # 树结构：root 下 2 个文件夹 + 1 个叶子
        self.tree = {
            "root": [
                {"node_token": "cat1", "title": "地缘政治", "has_child": True},
                {"node_token": "cat2", "title": "量化投资", "has_child": True},
                {"node_token": "leaf1", "title": "单篇笔记", "has_child": False},
            ],
            "cat1": [
                {"node_token": "d1", "title": "中美博弈", "has_child": False},
                {"node_token": "d2", "title": "关税战", "has_child": False},
            ],
            "cat2": [
                {"node_token": "d3", "title": "因子投资", "has_child": False},
            ],
        }

    def list_child_nodes(self, tok):
        self.calls += 1
        return self.tree.get(tok, [])


class TestCollectAllTitles:
    """_collect_all_titles 应利用 has_child 字段避免逐节点递归"""

    def test_collects_leaf_titles(self):
        fs = _fs()
        titles = fs._collect_all_titles(_HasChildClient(), "root")
        assert titles == {"中美博弈", "关税战", "因子投资", "单篇笔记"}
        # 文件夹节点自身标题不入集（只入叶子）

    def test_minimal_api_calls(self):
        """有 has_child 字段时：每节点 1 次 list，共 3 次（root/cat1/cat2），不递归叶子"""
        fs = _fs()
        client = _HasChildClient()
        fs._collect_all_titles(client, "root")
        # 3 个文件夹节点各调 1 次；2 个叶子节点不再调
        assert client.calls == 3

    def test_has_child_missing_fallback(self):
        """has_child 字段缺失（旧 API）→ 回退逐节点 list"""
        fs = _fs()
        client = _HasChildClient()
        # 移除 has_child 字段模拟旧 API
        for k in client.tree:
            for n in client.tree[k]:
                n.pop("has_child", None)
        titles = fs._collect_all_titles(client, "root")
        assert titles == {"中美博弈", "关税战", "因子投资", "单篇笔记"}
        # 回退路径：每个节点都需 list 判断是否文件夹；文件夹节点会被 list 两次
        # （bool 判断 1 次 + 递归内部 1 次），旧 API 兼容路径可接受
        # root(1) + cat1(2,3) + d1(4) + d2(5) + cat2(6,7) + d3(8) + leaf1(9) = 9 次
        assert client.calls == 9

    def test_empty_folder(self):
        """空文件夹不崩溃"""
        fs = _fs()
        client = _HasChildClient()
        client.tree["cat1"] = []
        titles = fs._collect_all_titles(client, "root")
        assert titles == {"因子投资", "单篇笔记"}


# ========== P1.4: _preflight ==========

class TestPreflight:
    """同步前预检：配置/授权/可达性 + 明确指令"""

    def test_ok_passes(self):
        """list_child_nodes 成功 → 不退出"""
        fs = _fs()
        with patch.object(fs, "_check_token_expiry") as m_tok:
            fs._preflight(MockClient(), "root")  # dry_run=False 但 Mock 无异常
        m_tok.assert_called_once()

    def test_dry_run_skips(self):
        """dry_run 模式跳过预检（不发起真实调用）"""
        fs = _fs()
        client = MockClient()
        client.dry_run = True
        with patch.object(fs, "_check_token_expiry") as m_tok:
            fs._preflight(client, "root")  # 不抛异常
        m_tok.assert_not_called()

    def test_not_configured_exits(self):
        """not_configured 错误 → 打印配置指令并退出"""
        fs = _fs()
        client = MockClient()
        client.list_child_nodes = lambda tok: (_ for _ in ()).throw(RuntimeError("lark-cli not configured"))
        with pytest.raises(SystemExit) as ei:
            fs._preflight(client, "root")
        assert ei.value.code == 1

    def test_unauthorized_exits(self):
        """授权过期 → 打印重新授权指令并退出"""
        fs = _fs()
        client = MockClient()
        client.list_child_nodes = lambda tok: (_ for _ in ()).throw(RuntimeError("unauthorized: token invalid"))
        with pytest.raises(SystemExit) as ei:
            fs._preflight(client, "root")
        assert ei.value.code == 1


# ========== P1.6: _check_token_expiry ==========
class TestTokenExpiry:
    """lark-cli auth status 解析：refresh token <2 天时提醒"""

    def test_refresh_soon_warns(self, capsys):
        """refresh token 剩余 1 天 → 打印警告"""
        fs = _fs()
        import datetime
        soon = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)).isoformat()
        payload = json.dumps({"identities": {"user": {"status": "ready", "refreshExpiresAt": soon}}})
        with patch.object(fs, "_find_lark_cli", return_value="lark-cli"), \
             patch("subprocess.run", return_value=type("R", (), {"returncode": 0, "stdout": payload})()):
            fs._check_token_expiry()
        out = capsys.readouterr().out
        assert "授权" in out and "过期" in out

    def test_refresh_far_silent_ok(self, capsys):
        """refresh token 剩余 5 天 → 打印有效，无警告"""
        fs = _fs()
        import datetime
        far = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=5)).isoformat()
        payload = json.dumps({"identities": {"user": {"status": "ready", "refreshExpiresAt": far}}})
        with patch.object(fs, "_find_lark_cli", return_value="lark-cli"), \
             patch("subprocess.run", return_value=type("R", (), {"returncode": 0, "stdout": payload})()):
            fs._check_token_expiry()
        out = capsys.readouterr().out
        assert "有效" in out

    def test_not_ready_warns(self, capsys):
        """身份未就绪 → 提示重新授权"""
        fs = _fs()
        payload = json.dumps({"identities": {"user": {"status": "expired"}}})
        with patch.object(fs, "_find_lark_cli", return_value="lark-cli"), \
             patch("subprocess.run", return_value=type("R", (), {"returncode": 0, "stdout": payload})()):
            fs._check_token_expiry()
        out = capsys.readouterr().out
        assert "重新授权" in out

    def test_bad_output_silent(self, capsys):
        """lark-cli 无输出/异常 → 静默跳过（非致命）"""
        fs = _fs()
        with patch.object(fs, "_find_lark_cli", return_value="lark-cli"), \
             patch("subprocess.run", return_value=type("R", (), {"returncode": 1, "stdout": ""})()):
            fs._check_token_expiry()  # 不抛异常
        out = capsys.readouterr().out
        assert out == ""
