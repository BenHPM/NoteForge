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

# progress
from noteforge.cli.commands.progress import run_progress_show, run_progress_clear

# single_note
from noteforge.cli.commands.single_note import run_single_note

# setup / doctor / validate_config
from noteforge.cli.commands.setup import run_setup
from noteforge.cli.commands.doctor import run_doctor, run_health_check, run_health_check_asr
from noteforge.cli.commands.validate_config import run_validate_config

# quality_view
from noteforge.cli.commands.quality_view import run_quality_view, run_quality_list

# feishu_auth
from noteforge.cli.commands.feishu_auth import run_feishu_auth, run_feishu_validate

# domain
from noteforge.cli.commands.domain import run_detect_domain, run_domain_list, run_incremental_update

# cleanup / provider status
from noteforge.cli.commands.cleanup import run_cleanup, run_provider_status
