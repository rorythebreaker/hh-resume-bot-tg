#!/usr/bin/env python3
"""Run the pinned upstream CLI with in-memory per-profile configuration."""
import json
import logging
import os
import sys
from functools import cached_property
from pathlib import Path
from hh_applicant_tool.tool import HHApplicantTool
from security import SecretFilter

class BotTool(HHApplicantTool):
    @cached_property
    def config(self):
        cfg = super().config
        overlay = json.loads(os.environ.get('HH_BOT_OVERLAY', '{}'))
        for key, value in overlay.items():
            cfg.set_value(key, value, create=True)
        return cfg

    def ai_chat_for(self, section=None, **defaults):
        settings = {**defaults, **self.get_openai_config(section)}
        client = self._get_ai_client(settings.pop('base_url'))
        client.max_retries = min(3, max(1, int(settings.pop('max_retries', 2))))
        client.proxy_url = settings.pop('proxy_url', None)
        if 'chat_completions_endpoint' in settings:
            client.chat_completions_endpoint = settings.pop('chat_completions_endpoint')
        return client.create_chat(settings.pop('system_prompt'), **settings)

    def setup_logging(self, verbosity=None):
        super().setup_logging(verbosity)
        for handler in logging.getLogger('hh_applicant_tool').handlers:
            handler.addFilter(SecretFilter())
        os.chmod(self.log_file, 0o600)

if __name__ == '__main__':
    os.umask(0o077)
    sys.exit(BotTool().run())
