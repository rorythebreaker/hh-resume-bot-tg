#!/usr/bin/env python3
"""Run the pinned upstream CLI with in-memory per-profile configuration."""
import json
import logging
import os
import sys
from functools import cached_property
from pathlib import Path
from hh_applicant_tool.tool import HHApplicantTool
from hh_applicant_tool.operations.apply_vacancies import Operation as ApplyOperation
from hh_applicant_tool.api.errors import LimitExceeded, Redirect
from security import SecretFilter

class ObservedApplyOperation(ApplyOperation):
    """Count upstream decisions, without duplicating its application logic."""
    def run(self, tool, args):
        self.metrics = {'found': 0, 'sent': 0, 'skipped': 0, 'errors': 0}
        try:
            return super().run(tool, args)
        finally:
            print('CORE_METRICS: ' + json.dumps(self.metrics), flush=True)

    def _get_vacancies(self, resume_id):
        for vacancy in super()._get_vacancies(resume_id=resume_id):
            self.metrics['found'] += 1
            yield vacancy

    def _process_vacancy(self, vacancy, resume, placeholders, seen_employers, state):
        before = state.applied_count
        try:
            result = super()._process_vacancy(vacancy, resume, placeholders, seen_employers, state)
        except (LimitExceeded, Redirect):
            self.metrics['skipped'] += 1
            raise
        except Exception:
            if state.applied_count == before:
                self.metrics['errors'] += 1
            raise
        else:
            if state.applied_count == before:
                self.metrics['skipped'] += 1
            return result
        finally:
            self.metrics['sent'] += state.applied_count - before

class BotTool(HHApplicantTool):
    def _solve_captcha(self, image_data):
        if os.environ.get('HH_BOT_CAPTCHA_UI') == '1':
            import apply_captcha, profiles_lib
            return apply_captcha.request(image_data, profiles_lib.get_active_profile()[0])
        return super()._solve_captcha(image_data)

    def _assign_args(self, args):
        super()._assign_args(args)
        if isinstance(getattr(args.operation_run, '__self__', None), ApplyOperation):
            self.operation_run = ObservedApplyOperation().run

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
