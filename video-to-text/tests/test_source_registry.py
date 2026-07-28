# -*- coding: utf-8 -*-
"""
NoteForge SourceRegistry 单元测试

覆盖 noteforge/sources/base.py 的 SourceRegistry 和 Source。

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_source_registry.py -v
"""
import os
import pytest
from noteforge.sources.base import Source, SourceRegistry, FetchResult


class FakeSource(Source):
    """用于测试的 fake source"""

    def __init__(self, name_suffix="", can_handle_fn=None, fetch_fn=None):
        self._name_suffix = name_suffix
        self._can_handle_fn = can_handle_fn or (lambda x: False)
        self._fetch_fn = fetch_fn

    def can_handle(self, input_str: str) -> bool:
        return self._can_handle_fn(input_str)

    def fetch(self, input_str: str, output_dir: str = "") -> FetchResult:
        if self._fetch_fn:
            return self._fetch_fn(input_str, output_dir)
        return FetchResult(title=f"fetched: {input_str}", source_type="fake")


class TestRegisterSource:
    """SourceRegistry.register 测试"""

    def test_register_source(self):
        """注册后 list_sources 包含名称"""
        registry = SourceRegistry()
        source = FakeSource(name_suffix="youtube")
        registry.register(source)
        names = registry.list_sources()
        assert "FakeSource" in names

    def test_register_multiple_sources(self):
        """注册多个 source 后全部列出"""
        registry = SourceRegistry()
        registry.register(FakeSource(can_handle_fn=lambda x: x.startswith("yt")))
        registry.register(FakeSource(can_handle_fn=lambda x: x.startswith("bv")))
        assert len(registry.list_sources()) == 2


class TestFetchRouting:
    """SourceRegistry.fetch 路由测试"""

    def test_fetch_routes_correctly(self):
        """输入被正确路由到对应的 Source"""
        registry = SourceRegistry()
        youtube_source = FakeSource(
            can_handle_fn=lambda x: x.startswith("https://youtube.com"),
            fetch_fn=lambda x, d: FetchResult(title="YouTube", source_type="youtube"),
        )
        bilibili_source = FakeSource(
            can_handle_fn=lambda x: x.startswith("https://bilibili.com"),
            fetch_fn=lambda x, d: FetchResult(title="Bilibili", source_type="bilibili"),
        )
        registry.register(youtube_source)
        registry.register(bilibili_source)

        result = registry.fetch("https://youtube.com/watch?v=123")
        assert result.source_type == "youtube"

        result = registry.fetch("https://bilibili.com/video/BV123")
        assert result.source_type == "bilibili"

    def test_fetch_unknown_raises(self):
        """无法识别的输入抛 ValueError"""
        registry = SourceRegistry()
        registry.register(FakeSource(can_handle_fn=lambda x: False))
        with pytest.raises(ValueError, match="无法识别输入"):
            registry.fetch("unknown://input")


class TestSourceName:
    """Source.name 属性测试"""

    def test_source_name_property(self):
        """name 返回类名"""
        source = FakeSource()
        assert source.name == "FakeSource"
