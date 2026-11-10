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
            if isinstance(tool,BotTool):
                tool._captcha_run_finished()
            print('CORE_METRICS: ' + json.dumps(self.metrics), flush=True)

    def _get_vacancies(self, resume_id):
        for vacancy in super()._get_vacancies(resume_id=resume_id):
            self.metrics['found'] += 1
            yield vacancy

    def _process_vacancy(self, vacancy, resume, placeholders, seen_employers, state):
        # Retain the public metadata already fetched by the application worker.
        # The notification watcher may not have a usable HH token of its own.
        try:
            from storage import atomic_write
            from hh_compat import validate_account
            import profiles_lib
            _, profile=profiles_lib.get_active_profile()
            vid=str(vacancy.get('id',''))
            if vid.isdigit() and profile:
                details=vacancy
                if not vacancy.get('description'):
                    try:
                        full=self.api_client.get('/vacancies/'+vid)
                        if isinstance(full,dict) and full.get('description'):
                            details={**vacancy,**full}
                    except Exception:
                        logging.getLogger(__name__).warning('Full vacancy description unavailable for %s',vid)
                account=validate_account(profile.get('hh_account','main'))
                path=Path(os.environ.get('CONFIG_DIR','/app/config'))/account/'.vacancy-meta'/(vid+'.json')
                atomic_write(path,json.dumps({'name':vacancy.get('name',''),
                    'employer':(vacancy.get('employer') or {}).get('name',''),
                    'description':str(details.get('description') or ''),
                    **{key:details.get(key) for key in ('salary','salary_range','work_format','experience','schedule','work_schedule_by_days','working_hours')},
                    'complete':bool(details.get('description'))},ensure_ascii=False))
        except (OSError,ValueError,TypeError):
            logging.getLogger(__name__).warning('Could not persist vacancy notification metadata')
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
            if state.applied_count > before and isinstance(getattr(self,'tool',None),BotTool):
                self.tool._application_succeeded()

class BotTool(HHApplicantTool):
    def _captcha_run_finished(self):
        if not getattr(self,'_captcha_pending_api',False):
            return
        import apply_captcha, profiles_lib
        pid=profiles_lib.get_active_profile()[0]
        latest=apply_captcha.status(pid)
        if getattr(self,'_captcha_pending_api',False) and latest and latest.get('state') in ('submitting','ocr'):
            apply_captcha.event(pid,'finished','Прогон завершён. Ответ CAPTCHA был отправлен, но прохождение этой проверки отдельно не подтверждено HH.')
        self._captcha_pending_api=False

    def _application_succeeded(self):
        self._captcha_ui_count=0
        self._captcha_auto_used=False
        if getattr(self,'_captcha_pending_api',False):
            import apply_captcha, profiles_lib
            self._captcha_pending_api=False
            apply_captcha.event(profiles_lib.get_active_profile()[0], 'accepted',
                'Проверка пройдена: HH принял отклик после CAPTCHA.')

    def _solve_captcha(self, image_data):
        if os.environ.get('HH_BOT_CAPTCHA_UI') == '1':
            import apply_captcha, profiles_lib
            self._captcha_ui_count = getattr(self, '_captcha_ui_count', 0) + 1
            if self._captcha_ui_count > 3:
                profile=profiles_lib.get_active_profile()[0]
                previous=apply_captcha.status(profile)
                detail=(' Последний результат: '+previous['message']) if previous else ''
                apply_captcha.event(profile, 'stopped', 'Прогон остановлен после трёх неподтверждённых CAPTCHA подряд.'+detail)
                raise SystemExit(6)
            try:
                pid,profile=profiles_lib.get_active_profile()
                if profiles_lib.get_ai(profile)['captcha_ocr'] and not getattr(self,'_captcha_auto_used',False):
                    self._captcha_auto_used=True
                    from captcha_ocr import recognize
                    apply_captcha.event(pid,'ocr','Пробуем распознать CAPTCHA локально.')
                    text=recognize(image_data,diagnostics=lambda value:print('OCR_DIAGNOSTIC: '+json.dumps(value),flush=True))
                    if text:
                        apply_captcha.event(pid,'submitting','OCR распознал текст. Проверяем ответ в HH.')
                        return text
                apply_captcha.event(pid,'waiting','Автоответ недоступен или HH запросил новую CAPTCHA. Нужен ручной ввод.')
                return apply_captcha.request(image_data, profiles_lib.get_active_profile()[0])
            except TimeoutError:
                raise SystemExit(4) from None
            except RuntimeError:
                apply_captcha.event(profiles_lib.get_active_profile()[0], 'cancelled', 'Ввод CAPTCHA отменён. Прогон остановлен.')
                raise SystemExit(5) from None
        return super()._solve_captcha(image_data)

    def _send_captcha(self, text, key, state, referer_url=None,
                      xsrf_token=None, backurl=None, session=None):
        if os.environ.get('HH_BOT_CAPTCHA_UI') != '1':
            return super()._send_captcha(text, key, state, referer_url=referer_url,
                xsrf_token=xsrf_token, backurl=backurl, session=session)
        from urllib.parse import urljoin, urlsplit, quote, unquote
        from hh_applicant_tool.tool import _CAPTCHA_HEADERS
        import apply_captcha, profiles_lib
        session = session or self.session
        target=urljoin(referer_url or self.base_url, '/account/captcha')
        failurl='/account/captcha?state='+quote(state)
        if backurl:
            failurl+='&backurl='+quote(backurl)
        payload={'captchaText':text,'captchaKey':key,'captchaState':state,
                 'backurl':backurl or '/','failurl':failurl}
        parts=urlsplit(target)
        headers=self._build_xhr_headers(
            origin=parts.scheme+'://'+parts.netloc,
            xsrf=xsrf_token or session.cookies.get('_xsrf'),
            referer_url=referer_url or parts.scheme+'://'+parts.netloc+'/',
            extra_headers=_CAPTCHA_HEADERS)
        response=session.request('POST',target,params=payload,
            headers=headers,allow_redirects=False,timeout=(10,30))
        # Keep only diagnostic flags and documented-style error codes. Never log
        # the raw body: it can contain challenge keys, answers and session data.
        details=[]
        try:
            body=response.json()
        except (ValueError, TypeError):
            body=None
        if isinstance(body,dict):
            challenge=body.get('hhcaptcha')
            if isinstance(challenge,dict):
                if isinstance(challenge.get('isBot'),bool):
                    details.append('isBot='+str(challenge['isBot']).lower())
                if challenge.get('captchaState'):
                    details.append('HH запросил новую проверку')
            import re
            codes=[]
            for entry in body.get('errors',[]) if isinstance(body.get('errors'),list) else []:
                if not isinstance(entry,dict):
                    continue
                for field in ('type','value'):
                    value=entry.get(field)
                    if isinstance(value,str) and re.fullmatch(r'[a-z_]{1,64}',value):
                        codes.append(value)
            if codes:
                details.append('коды HH: '+', '.join(codes[:6]))
        if not details:
            details.append('тело '+('JSON' if isinstance(body,dict) else 'не JSON'))
        destination=response.headers.get('Location','')
        actual=urlsplit(urljoin(target,unquote(destination))) if destination else None
        expected=urlsplit(urljoin(target,backurl or '/'))
        accepted=(response.status_code in (301,302,303,307,308)
                  and actual is not None
                  and not actual.username and not actual.password
                  and actual.scheme.lower()==expected.scheme.lower()
                  and actual.netloc.lower()==expected.netloc.lower()
                  and actual.path.rstrip('/')==expected.path.rstrip('/'))
        # HH's CAPTCHA endpoint can return 302 without Location. That is not
        # proof of rejection: let the protected API request verify the result.
        provisional=(response.status_code==302 and not destination
                     and (not isinstance(body,dict) or
                          not (body.get('errors') or body.get('hhcaptcha') or body.get('success') is False)))
        can_retry=bool(accepted or provisional)
        if can_retry:
            self._captcha_pending_api=True
        if actual:
            known_path=actual.path if actual.path in ('/','/account/captcha','/captcha','/captchaSuccessBackUrl') else '/[другой путь]'
            known_host=actual.hostname if actual.hostname in ('hh.ru','www.hh.ru','applicant') else '[другой хост]'
            known_scheme=actual.scheme if actual.scheme in ('http','https','hh') else '[другая схема]'
            details.append('Location: '+known_scheme+'://'+known_host+known_path)
        else:
            details.append('Location отсутствует')
        profile=profiles_lib.get_active_profile()[0]
        diagnostics=(
            '; HTTP '+str(response.status_code)+
            '; переход '+('успешный' if accepted else 'отсутствует или неожиданный')+
            '; '+ '; '.join(details))
        apply_captcha.event(profile,'submitting' if can_retry else 'rejected',
            'Ответ CAPTCHA отправлен. Продолжаем проверку в HH.' if can_retry else 'HH не подтвердил CAPTCHA. Если появится новая картинка, введите её текст вручную.',
            diagnostics=diagnostics)
        return can_retry

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
