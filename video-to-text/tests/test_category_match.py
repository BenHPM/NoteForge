"""
feishu_client.match_category 嵌套分类匹配单元测试

覆盖：
  - 扁平 match 列表匹配（文件名 vs 关键词）
  - 嵌套分类匹配
  - 无匹配时返回"其他笔记"

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_category_match.py -v
"""
import os
import pytest

os.environ['NOTEFORGE_SKIP_ENV_CHECK'] = '1'


class TestMatchCategory:
    """feishu_client.match_category 嵌套分类测试"""

    def test_flat_match(self):
        from noteforge.integration.feishu import match_category
        categories = [
            {"name": "技术", "match": ["*技术*", "*编程*"]},
            {"name": "其他笔记", "match": ["*"]},
        ]
        result = match_category("Python技术笔记.md", categories)
        assert result == "技术"

    def test_nested_match(self):
        from noteforge.integration.feishu import match_category
        categories = [
            {"name": "短视频导演课程", "match": ["*短视频*", "*第*集*"]},
            {"name": "其他笔记", "match": ["*"]},
        ]
        result = match_category("短视频创作笔记.md", categories)
        assert result == "短视频导演课程"

    def test_no_match_returns_other(self):
        from noteforge.integration.feishu import match_category
        categories = [
            {"name": "技术", "match": ["*技术*"]},
            {"name": "其他笔记", "match": ["*"]},
        ]
        result = match_category("随便什么.md", categories)
        assert result == "其他笔记"
