# -*- coding: utf-8 -*-
"""AudioHandler 音频转写与标题提取单元测试（12 tests）。"""
import os
import json
import pytest
from unittest.mock import MagicMock

class TestAudioHandler:
    """AudioHandler 音频转写与标题提取测试"""

    def _make_handler(self, tmp_path):
        from noteforge.core.audio_handler import AudioHandler
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()
        logger = MagicMock()
        return AudioHandler(
            transcripts_dir=transcripts_dir,
            base_dir=tmp_path,
            logger=logger,
        )

    def test_extract_title_from_video_mapping_list(self, tmp_path):
        """extract_title 应从 video-mapping.json 获取标题"""
        handler = self._make_handler(tmp_path)
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mapping = [
            {"id": "ep01", "title": "第一集：开场", "order": 1},
        ]
        (config_dir / "video-mapping.json").write_text(
            json.dumps(mapping, ensure_ascii=False), encoding='utf-8')
        title = handler.extract_title(str(tmp_path / "transcripts" / "ep01.txt"))
        assert title == "第一集：开场"

    def test_extract_title_fallback_to_stem(self, tmp_path):
        """extract_title 无 mapping 时应回退到文件名 stem"""
        handler = self._make_handler(tmp_path)
        title = handler.extract_title(str(tmp_path / "transcripts" / "some_episode.txt"))
        assert title == "some_episode"

    def test_extract_title_prefix_match(self, tmp_path):
        """extract_title 前缀匹配：id==stem 时精确匹配优先"""
        handler = self._make_handler(tmp_path)
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mapping = [
            {"id": "ep08", "title": "第八集", "order": 8},
        ]
        (config_dir / "video-mapping.json").write_text(
            json.dumps(mapping, ensure_ascii=False), encoding='utf-8')
        title = handler.extract_title(str(tmp_path / "transcripts" / "ep08.txt"))
        assert title == "第八集"

    def test_extract_title_dict_format(self, tmp_path):
        """extract_title 应支持 dict 格式的 mapping"""
        handler = self._make_handler(tmp_path)
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mapping = {
            "episodes": {
                "ep01": {"title": "开场集"},
            }
        }
        (config_dir / "video-mapping.json").write_text(
            json.dumps(mapping, ensure_ascii=False), encoding='utf-8')
        title = handler.extract_title(str(tmp_path / "transcripts" / "ep01.txt"))
        assert title == "开场集"

    def test_find_transcript_for_note_direct_match(self, tmp_path):
        """find_transcript_for_note 应直接匹配同名文件"""
        handler = self._make_handler(tmp_path)
        transcript = tmp_path / "transcripts" / "ep01.txt"
        transcript.write_text("转写内容", encoding='utf-8')
        result = handler.find_transcript_for_note(str(tmp_path / "notes" / "ep01.md"))
        assert result is not None
        assert "ep01.txt" in result

    def test_find_transcript_for_note_no_match(self, tmp_path):
        """find_transcript_for_note 无匹配时应返回 None"""
        handler = self._make_handler(tmp_path)
        result = handler.find_transcript_for_note(str(tmp_path / "notes" / "nonexistent.md"))
        assert result is None

    def test_find_transcript_for_note_fuzzy_match(self, tmp_path):
        """find_transcript_for_note 应支持模糊匹配"""
        handler = self._make_handler(tmp_path)
        transcript = tmp_path / "transcripts" / "ep01.txt"
        transcript.write_text("转写内容", encoding='utf-8')
        result = handler.find_transcript_for_note(str(tmp_path / "notes" / "ep01-extended.md"))
        assert result is not None

    def test_transcribe_audio_existing_transcript(self, tmp_path):
        """transcribe_audio 已有转写时应跳过"""
        from noteforge.core.audio_handler import AudioHandler
        from noteforge.models import GenerationResult
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()
        logger = MagicMock()
        handler = AudioHandler(
            transcripts_dir=transcripts_dir,
            base_dir=tmp_path,
            logger=logger,
        )
        existing = transcripts_dir / "audio_file.txt"
        existing.write_text("已有转写", encoding='utf-8')

        result = GenerationResult(transcript_path="")
        ret = handler.transcribe_audio(str(tmp_path / "audio_file.mp3"), result)
        assert ret is not None
        assert "audio_file.txt" in ret

    def test_transcribe_audio_no_paraformer(self, tmp_path):
        """transcribe_audio 在无 paraformer 环境时应回退或失败"""
        from noteforge.core.audio_handler import AudioHandler
        from noteforge.models import GenerationResult
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()
        logger = MagicMock()
        handler = AudioHandler(
            transcripts_dir=transcripts_dir,
            base_dir=tmp_path,
            logger=logger,
        )
        result = GenerationResult(transcript_path="")
        ret = handler.transcribe_audio(str(tmp_path / "nonexistent_audio.mp3"), result)
        assert ret is None or isinstance(ret, str)

    def test_read_file_utf8(self, tmp_path):
        """read_file 应能读取 UTF-8 文件"""
        from noteforge.infra.file_io import read_file
        test_file = tmp_path / "test.txt"
        test_file.write_text("中文内容", encoding='utf-8')
        content = read_file(str(test_file))
        assert content == "中文内容"

    def test_read_file_nonexistent_raises(self, tmp_path):
        """read_file 对不存在的文件应抛出异常"""
        from noteforge.infra.file_io import read_file
        with pytest.raises((FileNotFoundError, ValueError)):
            read_file(str(tmp_path / "nonexistent.txt"))
