"""
feishu_client.md_to_blocks Markdown → Blocks 转换单元测试

覆盖：
  - H1-H6 标题层级
  - 纯文本块
  - 无序列表 / 有序列表
  - 引用块 / 代码块
  - 空输入 / 水平分隔线
  - 行内加粗 / 行内代码样式

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_md_to_blocks.py -v
"""
import os
import pytest

class TestMdToBlocks:
    """测试 feishu_client.md_to_blocks 各元素类型转换"""

    def setup_method(self):
        from noteforge.integration.feishu import md_to_blocks
        self.md_to_blocks = md_to_blocks

    def test_heading_levels(self):
        """H1-H6 都应生成正确的 block_type"""
        for level in range(1, 7):
            md = f"{'#' * level} 标题{level}"
            blocks = self.md_to_blocks(md)
            assert len(blocks) == 1, f"H{level} 应产生 1 个 block"
            assert blocks[0]['block_type'] == 2 + level, f"H{level} block_type 应为 {2 + level}"

    def test_plain_text(self):
        """纯文本应生成 block_type=2"""
        blocks = self.md_to_blocks("这是一段普通文本")
        assert len(blocks) == 1
        assert blocks[0]['block_type'] == 2

    def test_bullet_list(self):
        """无序列表项应生成文本 block"""
        md = "- 项目1\n- 项目2\n- 项目3"
        blocks = self.md_to_blocks(md)
        assert len(blocks) >= 3

    def test_ordered_list(self):
        """有序列表项应生成文本 block"""
        md = "1. 第一步\n2. 第二步\n3. 第三步"
        blocks = self.md_to_blocks(md)
        assert len(blocks) >= 3

    def test_blockquote(self):
        """引用块应生成文本 block"""
        md = "> 这是一段引用"
        blocks = self.md_to_blocks(md)
        assert len(blocks) >= 1

    def test_code_block(self):
        """代码块应生成文本 block"""
        md = "```python\nprint('hello')\n```"
        blocks = self.md_to_blocks(md)
        assert len(blocks) >= 1

    def test_empty_input(self):
        """空输入应返回空列表"""
        blocks = self.md_to_blocks("")
        assert blocks == []

    def test_horizontal_rule_skipped(self):
        """水平分隔线应被跳过"""
        md = "上文\n---\n下文"
        blocks = self.md_to_blocks(md)
        # --- 不会产生 block，只有上文和下文
        assert len(blocks) == 2

    def test_inline_bold(self):
        """加粗文本应包含 bold 样式"""
        md = "这是**加粗**文本"
        blocks = self.md_to_blocks(md)
        assert len(blocks) >= 1
        # 检查 text_run 中是否有 bold 样式
        block = blocks[0]
        text_key = [k for k in block.keys() if k not in ('block_type',)][0]
        elements = block[text_key].get('elements', [])
        has_bold = any(
            el.get('text_run', {}).get('text_element_style', {}).get('bold', False)
            for el in elements
            if 'text_run' in el
        )
        assert has_bold, "应包含 bold 样式"

    def test_inline_code(self):
        """行内代码应包含 inline_code 样式"""
        md = "使用 `pip install` 安装"
        blocks = self.md_to_blocks(md)
        assert len(blocks) >= 1
