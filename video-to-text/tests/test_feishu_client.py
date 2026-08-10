# -*- coding: utf-8 -*-
"""
NoteForge FeishuClient 单元测试

通过 mock _api 方法测试飞书知识库客户端，不依赖外部 API/网络。

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_feishu_client.py -v
"""
import os
import pytest
from unittest.mock import patch, MagicMock, call

# 跳过 env_check

# ============================================================
# FeishuClient 测试
# ============================================================

class TestFeishuClient:
    """FeishuClient 飞书知识库客户端测试"""

    @pytest.fixture
    def client(self):
        """创建 dry_run 模式的 FeishuClient（不调用真实 API）"""
        from noteforge.integration.feishu import FeishuClient
        # 重置类变量以避免跨测试污染
        FeishuClient._lark_cli_path = "lark-cli"
        c = FeishuClient(space_id="test_space", dry_run=True)
        return c

    @pytest.fixture
    def live_client(self):
        """创建非 dry_run 的 FeishuClient，_api 被 mock"""
        from noteforge.integration.feishu import FeishuClient
        FeishuClient._lark_cli_path = "lark-cli"
        c = FeishuClient(space_id="test_space", dry_run=False)
        return c

    # ------ list_child_nodes ------

    def test_list_child_nodes(self, live_client):
        """list_child_nodes 应调用 _api 并返回子节点列表"""
        mock_items = [
            {"node_token": "n1", "title": "节点1"},
            {"node_token": "n2", "title": "节点2"},
        ]
        with patch.object(live_client, '_api', return_value={"data": {"items": mock_items}}) as mock_api:
            result = live_client.list_child_nodes("parent123")
            mock_api.assert_called_once_with(
                "GET",
                "wiki/v2/spaces/test_space/nodes",
                params={"parent_node_token": "parent123", "page_size": 50},
            )
            assert result == mock_items

    def test_list_child_nodes_empty(self, live_client):
        """list_child_nodes 无子节点时返回空列表"""
        with patch.object(live_client, '_api', return_value={"data": {"items": []}}):
            result = live_client.list_child_nodes("parent123")
            assert result == []

    def test_list_child_nodes_paginates(self, live_client):
        """list_child_nodes 超过 50 个子节点时应自动翻页合并（回归：地缘政治 107 篇重复创建 bug）"""
        page1 = [{"node_token": f"n{i}", "title": f"节点{i}"} for i in range(1, 51)]
        page2 = [{"node_token": f"n{i}", "title": f"节点{i}"} for i in range(51, 108)]
        responses = [
            {"data": {"items": page1, "page_token": "tok1", "has_more": True}},
            {"data": {"items": page2, "page_token": "", "has_more": False}},
        ]
        with patch.object(live_client, '_api', side_effect=responses) as mock_api:
            result = live_client.list_child_nodes("parent123")
            assert len(result) == 107
            assert mock_api.call_count == 2
            # 第一次无 page_token，第二次携带上一页的 page_token
            assert mock_api.call_args_list[0].kwargs["params"] == {
                "parent_node_token": "parent123", "page_size": 50,
            }
            assert mock_api.call_args_list[1].kwargs["params"] == {
                "parent_node_token": "parent123", "page_size": 50, "page_token": "tok1",
            }

    # ------ find_node_by_title ------

    def test_find_node_by_title_found(self, live_client):
        """find_node_by_title 找到标题对应的节点"""
        children = [
            {"node_token": "n1", "title": "跨集提炼"},
            {"node_token": "n2", "title": "逐集笔记"},
        ]
        with patch.object(live_client, 'list_child_nodes', return_value=children):
            result = live_client.find_node_by_title("parent123", "跨集提炼")
            assert result is not None
            assert result["node_token"] == "n1"

    def test_find_node_by_title_not_found(self, live_client):
        """find_node_by_title 标题不存在时返回 None"""
        children = [
            {"node_token": "n1", "title": "跨集提炼"},
        ]
        with patch.object(live_client, 'list_child_nodes', return_value=children):
            result = live_client.find_node_by_title("parent123", "不存在的标题")
            assert result is None

    def test_find_node_by_title_dry_run_returns_none(self, client):
        """dry_run 模式下 find_node_by_title 直接返回 None"""
        result = client.find_node_by_title("parent123", "任何标题")
        assert result is None

    # ------ create_node ------

    def test_create_node_creates_new(self, live_client):
        """create_node 在节点不存在时创建新节点"""
        new_node = {"node_token": "new123", "obj_token": "obj123", "title": "新节点"}
        with patch.object(live_client, 'find_node_by_title', return_value=None), \
             patch.object(live_client, '_api', return_value={"data": {"node": new_node}}) as mock_api:
            result = live_client.create_node("parent123", "新节点")
            mock_api.assert_called_once_with(
                "POST",
                "wiki/v2/spaces/test_space/nodes",
                data={
                    "obj_type": "docx",
                    "parent_node_token": "parent123",
                    "node_type": "origin",
                    "title": "新节点",
                },
            )
            assert result == new_node

    def test_create_node_finds_existing(self, live_client):
        """create_node 找到已有节点时不再创建"""
        existing = {"node_token": "exist123", "title": "已有节点"}
        with patch.object(live_client, 'find_node_by_title', return_value=existing), \
             patch.object(live_client, '_api') as mock_api:
            result = live_client.create_node("parent123", "已有节点")
            mock_api.assert_not_called()
            assert result == existing

    def test_create_node_dry_run(self, client):
        """dry_run 模式下 create_node 返回模拟节点"""
        result = client.create_node("parent123", "测试节点")
        assert "node_token" in result
        assert result["title"] == "测试节点"

    # ------ readonly 预览模式（2026-08-10：GET 真实放行、写拦截） ------

    @pytest.fixture
    def readonly_client(self):
        """dry_run + readonly 预览客户端（GET 真实读取、写操作 mock）"""
        from noteforge.integration.feishu import FeishuClient
        FeishuClient._lark_cli_path = "lark-cli"
        c = FeishuClient(space_id="test_space", dry_run=True, readonly=True)
        return c

    def test_api_readonly_get_passthrough(self, readonly_client):
        """readonly 模式下 GET 真实放行（调用 lark-cli 而非 mock）"""
        with patch('noteforge.integration.feishu.subprocess.run') as mock_run:
            mock_run.return_value.stdout = '{"ok": true, "data": {"items": []}}'
            mock_run.return_value.stderr = ""
            mock_run.return_value.returncode = 0
            readonly_client._api("GET", "wiki/v2/spaces/test_space/nodes")
            mock_run.assert_called_once()  # 真实执行了子进程调用

    def test_api_readonly_write_still_mocked(self, readonly_client):
        """readonly 模式下写操作（POST）仍 mock，不产生副作用"""
        with patch('noteforge.integration.feishu.subprocess.run') as mock_run:
            result = readonly_client._api("POST", "wiki/v2/spaces/test_space/nodes", data={})
            mock_run.assert_not_called()
            assert result == {"code": 0, "data": {}}

    def test_find_node_by_title_readonly_does_real_lookup(self, readonly_client):
        """readonly+dry_run 下 find_node_by_title 走真实子节点查找，而非直接返回 None"""
        children = [
            {"node_token": "n1", "title": "跨集提炼"},
        ]
        with patch.object(readonly_client, 'list_child_nodes', return_value=children):
            assert readonly_client.find_node_by_title("parent123", "跨集提炼") == children[0]
            assert readonly_client.find_node_by_title("parent123", "不存在") is None

    def test_create_node_readonly_not_found_returns_new_pseudo(self, readonly_client):
        """readonly 预览：节点不存在时返回伪 token（记录将创建），不调真实 POST"""
        with patch.object(readonly_client, 'find_node_by_title', return_value=None), \
             patch.object(readonly_client, '_api') as mock_api:
            result = readonly_client.create_node("parent123", "新分类")
            mock_api.assert_not_called()
            assert result["node_token"].startswith("dry-run-new-")
            assert result["title"] == "新分类"

    def test_create_node_readonly_found_returns_existing(self, readonly_client):
        """readonly 预览：节点已存在时返回真实节点，不重复创建"""
        existing = {"node_token": "exist123", "title": "已存在分类"}
        with patch.object(readonly_client, 'find_node_by_title', return_value=existing), \
             patch.object(readonly_client, '_api') as mock_api:
            result = readonly_client.create_node("parent123", "已存在分类")
            mock_api.assert_not_called()
            assert result == existing

    # ------ append_blocks ------

    def test_append_blocks_calls_api(self, live_client):
        """append_blocks 应分批调用 _api 写入 blocks"""
        blocks = [{"block_type": 2, "text": {"elements": []}}] * 3
        with patch.object(live_client, '_api', return_value={"code": 0}) as mock_api, \
             patch('time.sleep'):
            live_client.append_blocks("doc123", blocks)
            mock_api.assert_called_once_with(
                "POST",
                "docx/v1/documents/doc123/blocks/doc123/children",
                data={"children": blocks, "index": -1},
            )

    def test_append_blocks_batches_correctly(self, live_client):
        """append_blocks 应按 block_batch_size 分批写入"""
        live_client.block_batch_size = 2
        blocks = [{"block_type": 2}] * 5
        with patch.object(live_client, '_api', return_value={"code": 0}) as mock_api, \
             patch('time.sleep'):
            live_client.append_blocks("doc123", blocks)
            assert mock_api.call_count == 3  # 2+2+1

    def test_append_blocks_dry_run(self, client):
        """dry_run 模式下 append_blocks 不调用 _api"""
        blocks = [{"block_type": 2}]
        with patch.object(client, '_api') as mock_api:
            client.append_blocks("doc123", blocks)
            mock_api.assert_not_called()

    # ------ ensure_category_node ------

    def test_ensure_category_node_finds_existing(self, live_client):
        """ensure_category_node 找到已有分类时直接返回 node_token"""
        existing = {"node_token": "cat123", "title": "跨集提炼"}
        with patch.object(live_client, 'find_node_by_title', return_value=existing):
            result = live_client.ensure_category_node("root123", "跨集提炼")
            assert result == "cat123"

    def test_ensure_category_node_creates_new(self, live_client):
        """ensure_category_node 未找到分类时创建带前缀的新节点"""
        new_node = {"node_token": "newcat456", "title": "📁 跨集提炼"}
        with patch.object(live_client, 'find_node_by_title', return_value=None), \
             patch.object(live_client, 'create_node', return_value=new_node) as mock_create:
            result = live_client.ensure_category_node("root123", "跨集提炼")
            mock_create.assert_called_once_with("root123", "📁 跨集提炼", obj_type="docx")
            assert result == "newcat456"

    # ------ overwrite_document ------

    def test_overwrite_document_calls_delete_and_append(self, live_client):
        """overwrite_document 应先删除再追加"""
        blocks = [{"block_type": 2}]
        with patch.object(live_client, 'delete_block_children') as mock_delete, \
             patch.object(live_client, 'append_blocks') as mock_append, \
             patch('time.sleep'):
            live_client.overwrite_document("doc123", blocks)
            mock_delete.assert_called_once_with("doc123", "doc123")
            mock_append.assert_called_once_with("doc123", blocks)

    def test_overwrite_document_dry_run(self, client):
        """dry_run 模式下 overwrite_document 不调用删除和追加"""
        blocks = [{"block_type": 2}]
        with patch.object(client, 'delete_block_children') as mock_delete, \
             patch.object(client, 'append_blocks') as mock_append:
            client.overwrite_document("doc123", blocks)
            mock_delete.assert_not_called()
            mock_append.assert_not_called()

    # ------ create_document_and_write ------

    def test_create_document_and_write(self, live_client):
        """create_document_and_write 应创建节点后写入内容"""
        node = {"node_token": "n123", "obj_token": "obj456", "title": "测试文档"}
        blocks = [{"block_type": 2}]
        with patch.object(live_client, 'create_node', return_value=node), \
             patch.object(live_client, 'append_blocks') as mock_append, \
             patch('time.sleep'):
            result = live_client.create_document_and_write("parent123", "测试文档", blocks)
            mock_append.assert_called_once_with("obj456", blocks)
            assert result == "obj456"

    def test_create_document_and_write_no_blocks(self, live_client):
        """create_document_and_write 无 blocks 时跳过写入"""
        node = {"node_token": "n123", "obj_token": "obj456", "title": "空文档"}
        with patch.object(live_client, 'create_node', return_value=node), \
             patch.object(live_client, 'append_blocks') as mock_append:
            result = live_client.create_document_and_write("parent123", "空文档", [])
            mock_append.assert_not_called()
            assert result == "obj456"


# ============================================================
# md_to_blocks 测试
# ============================================================

class TestMdToBlocks:
    """Markdown → 飞书 Block 转换测试"""

    def test_heading_conversion(self):
        """Markdown 标题应转为对应 heading block"""
        from noteforge.integration.feishu import md_to_blocks
        blocks = md_to_blocks("## 二级标题")
        assert len(blocks) == 1
        assert blocks[0]["block_type"] == 4  # heading2 = 2+2
        assert "heading2" in blocks[0]

    def test_paragraph_conversion(self):
        """普通段落应转为 text block"""
        from noteforge.integration.feishu import md_to_blocks
        blocks = md_to_blocks("这是一段普通文本")
        assert len(blocks) == 1
        assert blocks[0]["block_type"] == 2
        assert "text" in blocks[0]

    def test_bullet_list_conversion(self):
        """Markdown 列表应转为带 bullet 的 text block"""
        from noteforge.integration.feishu import md_to_blocks
        blocks = md_to_blocks("- 列表项")
        assert len(blocks) == 1
        assert blocks[0]["block_type"] == 2
        # 列表转为普通文本，带 bullet 前缀
        elements = blocks[0]["text"]["elements"]
        assert any("•" in e.get("text_run", {}).get("content", "") for e in elements)

    def test_code_block_conversion(self):
        """代码块应转为 text block（保留代码标记）"""
        from noteforge.integration.feishu import md_to_blocks
        md = "```\ncode here\n```"
        blocks = md_to_blocks(md)
        assert len(blocks) == 1
        assert blocks[0]["block_type"] == 2

    def test_empty_input(self):
        """空输入应返回空列表"""
        from noteforge.integration.feishu import md_to_blocks
        assert md_to_blocks("") == []
        assert md_to_blocks("   \n\n  ") == []


# ============================================================
# match_category 测试
# ============================================================

class TestMatchCategory:
    """分类匹配测试"""

    def test_match_new_format(self):
        """新格式 match 列表匹配"""
        from noteforge.integration.feishu import match_category
        categories = [
            {"name": "量化投资", "match": ["*量化*", "*基金*"]},
            {"name": "地缘经济", "match": ["*地缘*", "*制裁*"]},
        ]
        assert match_category("量化投资入门", categories) == "量化投资"
        assert match_category("地缘政治分析", categories) == "地缘经济"

    def test_match_old_format(self):
        """旧格式单个 pattern 匹配"""
        from noteforge.integration.feishu import match_category
        categories = [
            {"name": "量化投资", "pattern": "*量化*"},
        ]
        assert match_category("量化投资入门", categories) == "量化投资"

    def test_match_fallback(self):
        """无匹配时返回 '其他笔记'"""
        from noteforge.integration.feishu import match_category
        categories = [
            {"name": "量化投资", "match": ["*量化*"]},
        ]
        assert match_category("短视频导演", categories) == "其他笔记"

    def test_match_first_hit_wins(self):
        """多个匹配时第一个命中生效"""
        from noteforge.integration.feishu import match_category
        categories = [
            {"name": "A", "match": ["*test*"]},
            {"name": "B", "match": ["*test*"]},
        ]
        assert match_category("test_file", categories) == "A"
