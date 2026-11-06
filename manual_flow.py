"""Preview and consume a single-use application confirmation with a pinned profile."""
import copy
import html
import re
import secrets
import threading
import time
from pathlib import Path
import profiles_lib
from storage import file_lock
from hh_api import HHAPIError

_pending = {}
_guard = threading.Lock()
_IDS = re.compile(r'(?:https?://(?:[a-z0-9-]+\.)?hh\.ru/vacancy/([0-9]+))|(?<![\w/])([0-9]{5,12})(?!\w)', re.I)

def parse_ids(text):
    return list(dict.fromkeys(a or b for a, b in _IDS.findall(text or '')))

def prepare(bot, chat_id, text):
    pid, profile = profiles_lib.get_active_profile()
    bot.PENDING['action'] = None
    if not profile or not profile.get('resume_id'):
        return 'Выберите профиль и резюме перед откликом.'
    ids = parse_ids(text)
    if not ids or len(ids) > 30:
        return 'Пришлите от 1 до 30 ссылок https://hh.ru/vacancy/123456789 или номеров вакансий.'
    profile = copy.deepcopy(profile)
    def runner():
        try:
            vacancies = []
            with profiles_lib.profile_context(pid, profile):
                for vid in ids:
                    v = bot.hh_get_strict(f'/vacancies/{vid}')
                    vacancies.append(v)
            token = secrets.token_hex(8)
            with _guard:
                _pending[str(chat_id)] = (token, time.monotonic() + 600, pid, profile, vacancies)
            lines = ['Подтвердите отклик', 'Профиль: ' + html.escape(profile.get('display_name', pid)),
                     'Резюме: ' + html.escape(profile['resume_id'])]
            for v in vacancies:
                lines.append(html.escape(str(v.get('name') or v.get('id'))) + ' ? ' + html.escape((v.get('employer') or {}).get('name', '')))
            rows = [[('Отправить без AI', f'manual:{token}:plain')]]
            if profiles_lib.get_ai(profile)['enabled']:
                rows.append([('Сгенерировать письмо AI и отправить', f'manual:{token}:ai')])
            rows.append([('Отмена', f'manual:{token}:cancel')])
            bot.send_menu(chat_id, {'title': '\n'.join(lines), 'rows': rows})
        except HHAPIError as exc:
            bot.send(chat_id, html.escape(str(exc)))
        except Exception:
            bot.send(chat_id, 'Не удалось подготовить отклик. Проверьте профиль и повторите.')
    threading.Thread(target=runner, daemon=True).start()
    return 'Получаю сведения о вакансиях для подтверждения.'

def confirm(bot, chat_id, data):
    _, token, mode = data.split(':', 2)
    with _guard:
        item = _pending.get(str(chat_id))
        if not item or item[0] != token or item[1] < time.monotonic():
            bot.send(chat_id, 'Кнопка устарела или уже использована. Подготовьте отклик заново.')
            return
        del _pending[str(chat_id)]
    if mode == 'cancel':
        bot.send(chat_id, 'Отклик отменён.'); return
    if mode not in ('plain', 'ai'): return
    _, _, pid, profile, vacancies = item
    def runner():
        sent = skipped = errors = 0
        try:
            with file_lock(bot.CONFIG_DIR / '.apply.lock', blocking=False), profiles_lib.profile_context(pid, profile):
                me = bot.hh_get_strict('/me')
                template = profiles_lib.get_letter_path(profile).read_text(encoding='utf-8')
                for old in vacancies:
                    if bot.is_paused(): break
                    vid = str(old['id'])
                    v = bot.hh_get_strict(f'/vacancies/{vid}')
                    if v.get('archived') or set(v.get('relations') or []) & {'got_response', 'got_rejection'}:
                        skipped += 1; continue
                    if v.get('has_test'):
                        skipped += 1
                        bot.send(chat_id, f'Вакансия {vid}: обязательный тест, завершите отклик на HH.'); continue
                    state = bot.compute_limits_state(bot.parse_log_applies(), bot.now_msk())
                    if not state['free_now']: break
                    letter = bot.render_letter(template, v, me)
                    if mode == 'ai': letter = generate_letter(profile, v)
                    ok, err = bot.hh_post_negotiation(vid, profile['resume_id'], letter)
                    if not ok:
                        errors += 1
                        bot.send(chat_id, html.escape(err))
                        break  # An ambiguous POST is never replayed automatically.
                    bot.append_tool_log(vid, profile['resume_id']); sent += 1
        except HHAPIError as exc:
            errors += 1; bot.send(chat_id, html.escape(str(exc)))
        except (BlockingIOError, PermissionError):
            bot.send(chat_id, 'Уже выполняется рассылка. Повторите после её завершения.'); return
        except Exception:
            errors += 1; bot.send(chat_id, 'Не удалось завершить отклик. Проверьте настройки и историю HH.')
        bot.send(chat_id, f'Отправлено: {sent}; пропущено: {skipped}; ошибок: {errors}; не обработано: {len(vacancies)-sent-skipped-errors}.')
    threading.Thread(target=runner, daemon=True).start()

def generate_letter(profile, vacancy):
    from hh_cli import BotTool
    from hh_applicant_tool.operations.apply_vacancies import DEFAULT_COVER_LETTER_SYSTEM_PROMPT, DEFAULT_COVER_LETTER_PROMPT
    from hh_applicant_tool.tool import BaseNamespace
    import os
    tool = BotTool()
    args = tool._parser.parse_args(['--config-dir', str(os.environ.get('CONFIG_DIR', '/app/config')), '--profile', profile['hh_account'], 'whoami'], namespace=BaseNamespace())
    tool._assign_args(args)
    ai = profiles_lib.get_ai(profile)
    chat = tool.ai_chat_for('openai.cover_letter', system_prompt=ai['system_prompt'] or DEFAULT_COVER_LETTER_SYSTEM_PROMPT, delay=60 / ai['rate_limit'], max_retries=2)
    context = tool.get_resume_placeholders(profile['resume_id'])
    context.update(vacancy_name=vacancy.get('name', ''), vacancy_description=vacancy.get('description', ''), employer_name=(vacancy.get('employer') or {}).get('name', ''))
    prompt = ai['message_prompt'] or DEFAULT_COVER_LETTER_PROMPT
    return chat.complete(prompt % context)
