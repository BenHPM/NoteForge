# -*- coding: utf-8 -*-
"""
NoteForge 批量质量评分工具单元测试

覆盖 noteforge/quality/batch.py 的 normalize、find_transcript_for_note、load_video_mapping。

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_batch_quality.py -v
"""
import os
os.environ['NOTEFORGE_SKIP_ENV_CHECK'] = '1'

import json
import pytest
from pathlib import Path

from noteforge.quality.batch import normalize, find_transcript_for_note, load_video_mapping


class TestNormalize:
    """normalize 函数测试"""

    def test_normalize_removes_brackets(self):
        """normalize 应去除方括号"""
        assert '[' not in normalize("测试[第1集]")
        assert ']' not in normalize("测试[第1集]")
        assert normalize("测试[第1集]") == "测试第1集"

    def test_normalize_removes_episode_prefix(self):
        """normalize 应去除标点和空格"""
        result = normalize("第1集：测试标题")
        assert '：' not in result
        assert '第1集' in result

    def test_normalize_empty_string(self):
        """normalize 空字符串返回空"""
        assert normalize("") == ""

    def test_normalize_removes_colons_and_punctuation(self):
        """normalize 应去除各种标点"""
        result = normalize("标题：副标题、其他，内容。")
        assert '：' not in result
        assert '、' not in result
        assert '，' not in result
        assert '。' not in result


class TestFindTranscriptForNote:
    """find_transcript_for_note 函数测试"""

    def test_find_transcript_direct_match(self, tmp_path):
        """直接匹配找到转写文件"""
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()
        (transcripts_dir / "ep01.txt").write_text("转写内容", encoding='utf-8')

        note_path = tmp_path / "notes" / "ep01.md"
        result = find_transcript_for_note(note_path, transcripts_dir, [])
        assert result is not None
        assert result.name == "ep01.txt"

    def test_find_transcript_mapping_match(self, tmp_path):
        """通过 video-mapping 找到转写文件"""
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()
        (transcripts_dir / "bv123.txt").write_text("转写内容", encoding='utf-8')

        video_mapping = [
            {"id": "bv123", "title": "第1集测试标题"}
        ]
        note_path = tmp_path / "notes" / "第1集测试标题.md"
        result = find_transcript_for_note(note_path, transcripts_dir, video_mapping)
        assert result is not None
        assert result.name == "bv123.txt"

    def test_find_transcript_episode_number_match(self, tmp_path):
        """通过集数编号匹配"""
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()
        (transcripts_dir / "bv456.txt").write_text("转写内容", encoding='utf-8')

        video_mapping = [
            {"id": "bv456", "title": "第5集其他标题"}
        ]
        note_path = tmp_path / "notes" / "第5集笔记标题.md"
        result = find_transcript_for_note(note_path, transcripts_dir, video_mapping)
        assert result is not None
        assert result.name == "bv456.txt"

    def test_find_transcript_no_match(self, tmp_path):
        """无匹配时返回 None"""
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()

        note_path = tmp_path / "notes" / "完全不匹配的标题.md"
        result = find_transcript_for_note(note_path, transcripts_dir, [])
        assert result is None


class TestLoadVideoMapping:
    """load_video_mapping 函数测试"""

    def test_load_video_mapping_valid_json(self, tmp_path):
        """加载有效 JSON 映射"""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mapping = [{"id": "ep01", "title": "第一集"}]
        (config_dir / "video-mapping.json").write_text(
            json.dumps(mapping, ensure_ascii=False), encoding='utf-8'
        )

        result = load_video_mapping(tmp_path)
        assert len(result) == 1
        assert result[0]['id'] == 'ep01'
        assert result[0]['title'] == '第一集'

    def test_load_video_mapping_missing_file(self, tmp_path):
        """映射文件不存在时返回空列表"""
        result = load_video_mapping(tmp_path)
        assert result == []
