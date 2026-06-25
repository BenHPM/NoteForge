"""
NoteForge 转写文本预处理模块 v2.1
功能:
- 清洗 Paraformer 转写文本中的噪声（[无法识别片段]、时间戳前缀等）
- 填充词清理（嗯、啊、那个、就是说 等）
- 连续标点规范化
- 估算 token 数量
- 超长文本分块（自适应大小 + 话题边界感知 + 渐进式重叠）
"""

import re
from typing import List, Optional


class TranscriptPreprocessor:
    """转写文本预处理器"""

    # 需要清理的噪声模式
    NOISE_PATTERNS = [
        (r'\[无法识别片段\]', ''),
        (r'\[\d{2}:\d{2}(:\d{2})?\]', ''),  # [00:00] 或 [00:00:00] 时间戳
        (r'\<\d+\.\d+\>', ''),               # <0.00> 速度标记
        (r'\[音乐\]', ''),
        (r'\[掌声\]', ''),
        (r'\[笑声\]', ''),
        (r'\[inaudible\]', ''),
        (r'\[silence\]', ''),
        # 新增噪声模式
        (r'\[咳嗽\]', ''),
        (r'\[cough\]', ''),
        (r'\[杂音\]', ''),
        (r'\[noise\]', ''),
        (r'\[听不清\]', ''),
        (r'\[呼吸\]', ''),
        (r'\[breath\]', ''),
        # 连续标点规范化（ASR 常见问题）
        (r'。。+', '。'),
        (r'，，+', '，'),
        (r'！！+', '！'),
        (r'？？+', '？'),
        (r'\.{3,}', '…'),
    ]

    # 填充词（口语中无意义的词）
    FILLER_PATTERNS = [
        (r'(?<![a-zA-Z])嗯(?![a-zA-Z])', ''),
        (r'(?<![a-zA-Z])啊(?![a-zA-Z])', ''),
        (r'(?<![a-zA-Z])呃(?![a-zA-Z])', ''),
        # 叠词填充（当前单字模式不覆盖）
        (r'嗯嗯+', ''),
        (r'啊啊+', ''),
        (r'呃呃+', ''),
        # 口头肯定叠加
        (r'对对对+', '对'),
        (r'是是是+', '是'),
        # 重复口头禅
        (r'这个这个+', '这个'),
        # "那个"仅在句首/逗号后清理（避免误删"那个产品"中的指代）
        (r'(?:^|[，。、；\s])那个(?=[，。、；\s])', ''),
        (r'^那个(?=[，。、；\s])', ''),
        (r'就是说', ''),
        (r'然后呢', '然后'),
        (r'对吧', ''),
        (r'你知道吗', ''),
        (r'是不是', ''),
        (r'怎么说呢', ''),
        (r'反正就是', '就是'),
        # 英文 filler（中英混合演讲场景）
        (r'(?<![a-zA-Z])you know(?![a-zA-Z])', ''),
        (r'(?<![a-zA-Z])I mean(?![a-zA-Z])', ''),
        (r'(?<![a-zA-Z])basically(?![a-zA-Z])', ''),
    ]

    # 话题切换信号词（用于边界检测）
    TOPIC_SIGNALS = [
        r'好[了的]?(?:，|,|\s)',         # "好，" / "好了，"
        r'接下来',                        # "接下来我们..."
        r'下一个',                        # "下一个话题"
        r'我们来[看看说聊讲]',            # "我们来看看..."
        r'第[一二三四五六七八九十\d]+[点个步]',  # "第一点" / "第二个"
        r'首先.{0,5}(?:我们|大家)',       # "首先我们..."
        r'另外',                          # "另外..."
        r'还[有是]',                       # "还有..." / "还是..."
        r'(?:再)?[换转]个?[话主]题',       # "换个话题"
        r'回到.{0,10}(?:问题|话题)',       # "回到刚才的问题"
    ]

    # 中文句子结束符
    SENTENCE_ENDINGS = re.compile(r'[。！？\n]')

    def __init__(self, tiktoken_model: str = "cl100k_base"):
        """
        Args:
            tiktoken_model: tiktoken 编码模型名
        """
        self._tiktoken_model = tiktoken_model
        self._encoder = None
        self._topic_signal_re = re.compile(
            '|'.join(self.TOPIC_SIGNALS), re.IGNORECASE
        )

    def _get_encoder(self):
        """延迟加载 tiktoken encoder"""
        if self._encoder is None:
            import tiktoken
            self._encoder = tiktoken.get_encoding(self._tiktoken_model)
        return self._encoder

    def clean(self, raw_text: str, clean_fillers: bool = True) -> str:
        """
        清洗转写文本

        Args:
            raw_text: 原始转写文本
            clean_fillers: 是否清理填充词（嗯、啊、那个等）

        Returns:
            清洗后的文本
        """
        text = raw_text

        # 应用噪声清理模式
        for pattern, replacement in self.NOISE_PATTERNS:
            text = re.sub(pattern, replacement, text)

        # 清理填充词
        if clean_fillers:
            for pattern, replacement in self.FILLER_PATTERNS:
                text = re.sub(pattern, replacement, text)

        # 合并连续空行为单个换行
        text = re.sub(r'\n{3,}', '\n\n', text)

        # 去除行首尾空白
        lines = [line.strip() for line in text.split('\n')]
        text = '\n'.join(lines)

        # 去除多余空格（保留单个空格）
        text = re.sub(r' {2,}', ' ', text)

        return text.strip()

    def estimate_tokens(self, text: str) -> int:
        """
        估算文本 token 数量

        Args:
            text: 输入文本

        Returns:
            估算的 token 数
        """
        encoder = self._get_encoder()
        return len(encoder.encode(text))

    def chunk_if_needed(
        self,
        text: str,
        max_tokens: int = 50000,
        overlap_tokens: int = 1000,
        min_chunk_size: int = 5000
    ) -> List[str]:
        """
        如果文本超过 max_tokens，按句子边界分块
        优先在话题切换处分块（话题边界感知）

        Args:
            text: 输入文本
            max_tokens: 单块最大 token 数
            overlap_tokens: 块间重叠 token 数（长内容建议 1000+）
            min_chunk_size: 最小块大小（token）

        Returns:
            文本块列表。如果文本未超限，返回 [text]
        """
        estimated = self.estimate_tokens(text)
        if estimated <= max_tokens:
            return [text]

        # 按句子边界分割，标记每个句子是否是话题切换点
        sentences = self._split_sentences(text)
        if not sentences:
            return [text]

        # 标记话题切换点
        topic_flags = self._detect_topic_boundaries(sentences)

        # 贪心分块：优先在话题边界处切分
        encoder = self._get_encoder()
        chunks: List[str] = []
        current_sentences: List[str] = []
        current_tokens = 0
        sentence_offset = 0  # 已处理句子在原始列表中的偏移量

        for i, sent in enumerate(sentences):
            sent_tokens = len(encoder.encode(sent))

            if current_tokens + sent_tokens > max_tokens and current_tokens >= min_chunk_size:
                # 当前块已满，寻找最近的话题边界作为切分点
                split_idx = self._find_best_split_point(
                    current_sentences, topic_flags[sentence_offset:sentence_offset + len(current_sentences)],
                    encoder, min_chunk_size
                )

                if split_idx > 0 and split_idx < len(current_sentences):
                    # 在话题边界切分
                    chunks.append('\n'.join(current_sentences[:split_idx]))
                    overlap_sents = current_sentences[split_idx:]
                    sentence_offset += split_idx
                else:
                    # 无合适话题边界，按原策略切分
                    chunks.append('\n'.join(current_sentences))
                    overlap_sents = self._get_overlap_sentences(
                        current_sentences, encoder, overlap_tokens
                    )
                    sentence_offset += len(current_sentences)

                current_sentences = overlap_sents
                current_tokens = sum(len(encoder.encode(s)) for s in overlap_sents)

            current_sentences.append(sent)
            current_tokens += sent_tokens

        # 最后一块
        if current_sentences:
            chunks.append('\n'.join(current_sentences))

        return chunks if chunks else [text]

    def compute_adaptive_chunk_size(
        self,
        context_limit: int,
        system_prompt_tokens: int = 2000,
        output_tokens: int = 8192,
        buffer: int = 2000
    ) -> int:
        """
        根据 LLM 上下文窗口自适应计算分块大小

        Args:
            context_limit: LLM 上下文窗口大小（token）
            system_prompt_tokens: system prompt 占用
            output_tokens: 预期输出大小
            buffer: 安全缓冲

        Returns:
            建议的单块最大 token 数
        """
        available = context_limit - system_prompt_tokens - output_tokens - buffer
        # 最小 10K，最大 80K
        return max(10000, min(80000, available))

    def _split_sentences(self, text: str) -> List[str]:
        """
        按中文句子边界分割文本

        Args:
            text: 输入文本

        Returns:
            句子列表
        """
        # 按句号、感叹号、问号、换行分割，保留分隔符
        parts = self.SENTENCE_ENDINGS.split(text)
        delimiters = self.SENTENCE_ENDINGS.findall(text)

        sentences: List[str] = []
        for i, part in enumerate(parts):
            part = part.strip()
            if not part:
                continue
            # 重新附上分隔符
            if i < len(delimiters):
                part += delimiters[i]
            sentences.append(part)

        return sentences

    def _get_overlap_sentences(
        self,
        sentences: List[str],
        encoder,
        overlap_tokens: int
    ) -> List[str]:
        """
        从句子列表末尾回溯，取出约 overlap_tokens 个 token 的句子

        Args:
            sentences: 句子列表
            encoder: tiktoken encoder
            overlap_tokens: 目标重叠 token 数

        Returns:
            末尾的若干句子
        """
        overlap_sents: List[str] = []
        token_count = 0

        for sent in reversed(sentences):
            sent_tokens = len(encoder.encode(sent))
            if token_count + sent_tokens > overlap_tokens:
                break
            overlap_sents.append(sent)
            token_count += sent_tokens

        overlap_sents.reverse()
        return overlap_sents

    def _detect_topic_boundaries(self, sentences: List[str]) -> List[bool]:
        """
        检测句子列表中的话题切换点

        Args:
            sentences: 句子列表

        Returns:
            与 sentences 等长的布尔列表，True 表示该句是话题切换点
        """
        flags: List[bool] = []
        for sent in sentences:
            # 检查是否包含话题切换信号词
            is_boundary = bool(self._topic_signal_re.search(sent))
            flags.append(is_boundary)
        return flags

    def _find_best_split_point(
        self,
        sentences: List[str],
        topic_flags: List[bool],
        encoder,
        min_chunk_size: int
    ) -> int:
        """
        在当前块中找到最佳切分点（优先话题边界）

        Args:
            sentences: 当前块的句子列表
            topic_flags: 对应的话题边界标记
            encoder: tiktoken encoder
            min_chunk_size: 最小块大小

        Returns:
            切分点索引（切分点之前的句子属于当前块）
        """
        if not topic_flags:
            return 0

        # 从后往前找最近的话题边界（但要保证剩余部分 >= min_chunk_size）
        accumulated_tokens = 0
        best_split = 0

        for i in range(len(sentences) - 1, -1, -1):
            sent_tokens = len(encoder.encode(sentences[i]))
            accumulated_tokens += sent_tokens

            flag_idx = i if i < len(topic_flags) else -1
            if flag_idx >= 0 and topic_flags[flag_idx]:
                remaining_tokens = sum(
                    len(encoder.encode(s)) for s in sentences[:i]
                )
                if remaining_tokens >= min_chunk_size:
                    best_split = i
                    break

        return best_split

    def get_transcript_stats(self, text: str) -> dict:
        """
        获取转写文本统计信息

        Args:
            text: 输入文本

        Returns:
            统计信息字典
        """
        estimated_tokens = self.estimate_tokens(text)
        char_count = len(text.replace('\n', '').replace(' ', ''))
        line_count = text.count('\n') + 1

        return {
            'char_count': char_count,
            'line_count': line_count,
            'estimated_tokens': estimated_tokens,
            'needs_chunking': estimated_tokens > 50000,
        }
