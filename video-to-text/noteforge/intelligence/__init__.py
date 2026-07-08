# -*- coding: utf-8 -*-
"""NoteForge LLM + 知识合成层"""

from noteforge.intelligence.synthesis import SynthesisEngine
from noteforge.intelligence.knowledge_index import KnowledgeIndex
from noteforge.intelligence.prompts import (
    build_synthesis_system_prompt,
    build_synthesis_prompt,
    build_extraction_prompt,
    build_merge_prompt,
    build_incremental_update_prompt,
    build_contradiction_detection_prompt,
)
from noteforge.intelligence.validation import validate_synthesis
