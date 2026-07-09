# -*- coding: utf-8 -*-
"""NoteForge CLI 各模式的执行逻辑子包"""

# _shared
from noteforge.cli.commands._shared import _show_cached_quality

# check
from noteforge.cli.commands.check import run_check_only

# search
from noteforge.cli.commands.search import run_search, run_list_notes

# sources
from noteforge.cli.commands.sources import (
    run_youtube,
    run_youtube_playlist,
    run_bilibili,
    run_audio_url,
    run_local,
)

# synthesis
from noteforge.cli.commands.synthesis import (
    run_synthesis,
    run_synthesis_2stage,
    run_synthesis_incremental,
)

# podcast
from noteforge.cli.commands.podcast import (
    _get_podcast_dirs,
    run_podcast_subscribe,
    run_podcast_unsubscribe,
    run_podcast_list,
    run_podcast_sync,
    run_podcast_sync_all,
    run_podcast_process,
)

# batch
from noteforge.cli.commands.batch_cmd import run_batch

# single_note
from noteforge.cli.commands.single_note import run_single_note
