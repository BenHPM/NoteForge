"""
video-mapping.json 数据完整性单元测试

覆盖：
  - 无重复标题
  - 所有条目包含 id/title/order
  - order 连续递增

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_video_mapping.py -v
"""
import json
import os
import pytest
from pathlib import Path

class TestVideoMapping:
    """video-mapping.json 数据完整性测试"""

    @pytest.fixture
    def mapping(self):
        config_dir = Path(__file__).parent.parent / "config"
        with open(config_dir / "video-mapping.json", encoding="utf-8") as f:
            return json.load(f)

    def test_no_duplicate_titles(self, mapping):
        """验证无重复标题（修复后的集数冲突）"""
        titles = [entry["title"] for entry in mapping]
        # 允许有相似但不完全相同的标题
        assert len(titles) == len(set(titles)), f"存在重复标题: {[t for t in titles if titles.count(t) > 1]}"

    def test_all_entries_have_required_fields(self, mapping):
        for entry in mapping:
            assert "id" in entry
            assert "title" in entry
            assert "order" in entry

    def test_order_is_sequential(self, mapping):
        orders = [entry["order"] for entry in mapping]
        assert orders == list(range(1, len(mapping) + 1))
