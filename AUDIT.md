# Item-by-item requirements audit

Date: 2026-11-06. Each original requirement below has its own row. IMPLEMENTED means code exists and the cited evidence was inspected; it does not establish live external-service behavior. LIVE CHECK REQUIRED means an authorized HH/AI account is needed. PARTIAL identifies an actual limitation.

Local validation: 47 tests passed on Python 3.13. CI targets Python 3.12, Docker build and three-service startup with a local Telegram stub. CI passed: https://github.com/rorythebreaker/hh-resume-bot-tg/actions/runs/42242936205 . Versioned prerelease: https://github.com/rorythebreaker/hh-resume-bot-tg/releases/tag/v2.5.4-bot.1 .


## 1. Обновление ядра

| ID | Requirement | Status and evidence |
|---|---|---|
| 1.1 | Использовать `hh-applicant-tool 2.5.4`. | **IMPLEMENTED.** requirements.txt, Dockerfile, hh_cli.py; actual 2.5.4 parser test. |
| 1.2 | Перейти на Python 3.12. | **IMPLEMENTED.** requirements.txt, Dockerfile, hh_cli.py; actual 2.5.4 parser test. |
| 1.3 | Адаптировать все вызовы CLI к новой версии. | **IMPLEMENTED.** requirements.txt, Dockerfile, hh_cli.py; actual 2.5.4 parser test. |
| 1.4 | Удалить устаревшие Playwright-патчи, если их функциональность уже реализована в ядре. | **IMPLEMENTED.** requirements.txt, Dockerfile, hh_cli.py; actual 2.5.4 parser test. |
| 1.5 | Удалить дублирующие функции, сохранив возможности Telegram-бота. | **IMPLEMENTED.** requirements.txt, Dockerfile, hh_cli.py; actual 2.5.4 parser test. |
| 1.6 | Обеспечить совместимость `run-apply.py`, `auth_helper.py`, `tg_bot.py` и `tg_watcher.py`. | **IMPLEMENTED.** requirements.txt, Dockerfile, hh_cli.py; actual 2.5.4 parser test. |
| 1.7 | Проверить корректную работу всех команд после обновления. | **LIVE CHECK REQUIRED.** requirements.txt, Dockerfile, hh_cli.py; actual 2.5.4 parser test. |

## 2. Авторизация и профили HH.ru

| ID | Requirement | Status and evidence |
|---|---|---|
| 2.1 | Поддерживать авторизацию через актуальный механизм HH Applicant Tool. | **LIVE CHECK REQUIRED.** hh_compat.py, profiles_lib.py, migrate_config.py, hh_api.py, auth_helper.py; isolation and migration tests. |
| 2.2 | Корректно работать с `token.json` и `config.toml`. | **IMPLEMENTED.** hh_compat.py, profiles_lib.py, migrate_config.py, hh_api.py, auth_helper.py; isolation and migration tests. |
| 2.3 | Реализовать миграцию старых конфигураций. | **IMPLEMENTED.** hh_compat.py, profiles_lib.py, migrate_config.py, hh_api.py, auth_helper.py; isolation and migration tests. |
| 2.4 | Поддерживать несколько аккаунтов HH.ru. | **IMPLEMENTED.** hh_compat.py, profiles_lib.py, migrate_config.py, hh_api.py, auth_helper.py; isolation and migration tests. |
| 2.5 | Позволять переключать аккаунты через Telegram. | **IMPLEMENTED.** hh_compat.py, profiles_lib.py, migrate_config.py, hh_api.py, auth_helper.py; isolation and migration tests. |
| 2.6 | Проверять действительность токена. | **LIVE CHECK REQUIRED.** hh_compat.py, profiles_lib.py, migrate_config.py, hh_api.py, auth_helper.py; isolation and migration tests. |
| 2.7 | Обновлять токен при истечении срока действия. | **LIVE CHECK REQUIRED.** hh_compat.py, profiles_lib.py, migrate_config.py, hh_api.py, auth_helper.py; isolation and migration tests. |
| 2.8 | Уведомлять о необходимости повторной авторизации. | **IMPLEMENTED.** hh_compat.py, profiles_lib.py, migrate_config.py, hh_api.py, auth_helper.py; isolation and migration tests. |
| 2.9 | Не раскрывать токены, пароли и API-ключи в логах. | **IMPLEMENTED.** hh_compat.py, profiles_lib.py, migrate_config.py, hh_api.py, auth_helper.py; isolation and migration tests. |

## 3. Автоматические отклики

| ID | Requirement | Status and evidence |
|---|---|---|
| 3.1 | Искать вакансии по заданным параметрам. | **IMPLEMENTED.** run-apply.py, schedule_config.py, apply_with_notify.sh, run_history.py; upstream search/application engine. |
| 3.2 | Выбирать резюме для откликов. | **IMPLEMENTED.** run-apply.py, schedule_config.py, apply_with_notify.sh, run_history.py; upstream search/application engine. |
| 3.3 | Фильтровать вакансии по ключевым словам, региону, зарплате, опыту и формату работы. | **IMPLEMENTED.** run-apply.py, schedule_config.py, apply_with_notify.sh, run_history.py; upstream search/application engine. |
| 3.4 | Исключать нежелательные вакансии и работодателей. | **IMPLEMENTED.** run-apply.py, schedule_config.py, apply_with_notify.sh, run_history.py; upstream search/application engine. |
| 3.5 | Исключать уже обработанные вакансии. | **IMPLEMENTED.** run-apply.py, schedule_config.py, apply_with_notify.sh, run_history.py; upstream search/application engine. |
| 3.6 | Поддерживать сопроводительные письма. | **IMPLEMENTED.** run-apply.py, schedule_config.py, apply_with_notify.sh, run_history.py; upstream search/application engine. |
| 3.7 | Использовать AI для анализа вакансий и формирования писем. | **LIVE CHECK REQUIRED.** run-apply.py, schedule_config.py, apply_with_notify.sh, run_history.py; upstream search/application engine. |
| 3.8 | Ограничивать количество откликов. | **IMPLEMENTED.** run-apply.py, schedule_config.py, apply_with_notify.sh, run_history.py; upstream search/application engine. |
| 3.9 | Поддерживать запуск по расписанию. | **IMPLEMENTED.** run-apply.py, schedule_config.py, apply_with_notify.sh, run_history.py; upstream search/application engine. |
| 3.10 | Позволять запускать и останавливать обработку через Telegram. | **IMPLEMENTED.** run-apply.py, schedule_config.py, apply_with_notify.sh, run_history.py; upstream search/application engine. |
| 3.11 | Формировать отчёт об успешных, пропущенных и неудачных откликах. | **IMPLEMENTED.** run-apply.py, schedule_config.py, apply_with_notify.sh, run_history.py; upstream search/application engine. |

## 4. Ручные отклики через Telegram

| ID | Requirement | Status and evidence |
|---|---|---|
| 4.1 | Принимать ссылки вида `https://hh.ru/vacancy/139159350`. | **IMPLEMENTED.** manual_flow.py, hh_api.py, tg_bot.py; preview, confirmation and sender-pinning tests. |
| 4.2 | Корректно извлекать ID вакансии. | **IMPLEMENTED.** manual_flow.py, hh_api.py, tg_bot.py; preview, confirmation and sender-pinning tests. |
| 4.3 | Получать сведения о вакансии через HH API. | **IMPLEMENTED.** manual_flow.py, hh_api.py, tg_bot.py; preview, confirmation and sender-pinning tests. |
| 4.4 | Не путать ошибки доступа с отсутствием вакансии. | **IMPLEMENTED.** manual_flow.py, hh_api.py, tg_bot.py; preview, confirmation and sender-pinning tests. |
| 4.5 | Позволять выбирать аккаунт и резюме. | **IMPLEMENTED.** manual_flow.py, hh_api.py, tg_bot.py; preview, confirmation and sender-pinning tests. |
| 4.6 | Показывать информацию о вакансии перед откликом. | **IMPLEMENTED.** manual_flow.py, hh_api.py, tg_bot.py; preview, confirmation and sender-pinning tests. |
| 4.7 | Генерировать сопроводительное письмо. | **LIVE CHECK REQUIRED.** manual_flow.py, hh_api.py, tg_bot.py; preview, confirmation and sender-pinning tests. |
| 4.8 | Позволять отправлять отклик без AI. | **IMPLEMENTED.** manual_flow.py, hh_api.py, tg_bot.py; preview, confirmation and sender-pinning tests. |
| 4.9 | Предотвращать повторные отклики. | **IMPLEMENTED.** manual_flow.py, hh_api.py, tg_bot.py; preview, confirmation and sender-pinning tests. |
| 4.10 | Обрабатывать анкеты и обязательные вопросы в пределах возможностей API. | **PARTIAL.** Required manual tests are handed off to HH; automatic tests use upstream. No interactive questionnaire builder. |
| 4.11 | Показывать точный результат отправки. | **IMPLEMENTED.** manual_flow.py, hh_api.py, tg_bot.py; preview, confirmation and sender-pinning tests. |

## 5. Ошибки HH API и CAPTCHA

| ID | Requirement | Status and evidence |
|---|---|---|
| 5.1 | Раздельно обрабатывать HTTP 400, 401, 403, 404, 429 и 5xx. | **IMPLEMENTED.** hh_api.py, auth_helper.py, run-apply.py; three GET attempts, no ambiguous POST replay. |
| 5.2 | Различать ошибки авторизации, ограничения доступа, CAPTCHA и отсутствие вакансии. | **IMPLEMENTED.** hh_api.py, auth_helper.py, run-apply.py; three GET attempts, no ambiguous POST replay. |
| 5.3 | Использовать штатный механизм CAPTCHA из версии 2.5.4. | **LIVE CHECK REQUIRED.** hh_api.py, auth_helper.py, run-apply.py; three GET attempts, no ambiguous POST replay. |
| 5.4 | Проверять конфигурацию AI-провайдера, используемого для CAPTCHA. | **LIVE CHECK REQUIRED.** hh_api.py, auth_helper.py, run-apply.py; three GET attempts, no ambiguous POST replay. |
| 5.5 | Предусмотреть ручное прохождение CAPTCHA, когда это поддерживается. | **LIVE CHECK REQUIRED.** hh_api.py, auth_helper.py, run-apply.py; three GET attempts, no ambiguous POST replay. |
| 5.6 | Не запускать бесконечные повторные попытки. | **IMPLEMENTED.** hh_api.py, auth_helper.py, run-apply.py; three GET attempts, no ambiguous POST replay. |
| 5.7 | Применять ограниченные повторы с задержками для временных ошибок. | **IMPLEMENTED.** hh_api.py, auth_helper.py, run-apply.py; three GET attempts, no ambiguous POST replay. |
| 5.8 | Приостанавливать отклики при ограничениях HH. | **IMPLEMENTED.** hh_api.py, auth_helper.py, run-apply.py; three GET attempts, no ambiguous POST replay. |
| 5.9 | Отправлять уведомление в Telegram с причиной остановки. | **IMPLEMENTED.** hh_api.py, auth_helper.py, run-apply.py; three GET attempts, no ambiguous POST replay. |
| 5.10 | Корректно возобновлять обработку после устранения проблемы. | **LIVE CHECK REQUIRED.** hh_api.py, auth_helper.py, run-apply.py; three GET attempts, no ambiguous POST replay. |

## 6. Telegram-интерфейс

| ID | Requirement | Status and evidence |
|---|---|---|
| 6.1 | Главное меню с inline-кнопками. | **IMPLEMENTED.** tg_bot.py, manual_flow.py, schedule_config.py; callback tests. |
| 6.2 | Управление аккаунтами. | **IMPLEMENTED.** tg_bot.py, manual_flow.py, schedule_config.py; callback tests. |
| 6.3 | Управление резюме. | **IMPLEMENTED.** tg_bot.py, manual_flow.py, schedule_config.py; callback tests. |
| 6.4 | Настройка параметров поиска. | **IMPLEMENTED.** tg_bot.py, manual_flow.py, schedule_config.py; callback tests. |
| 6.5 | Настройка автоматических откликов. | **IMPLEMENTED.** tg_bot.py, manual_flow.py, schedule_config.py; callback tests. |
| 6.6 | Ручные отклики по ссылкам. | **IMPLEMENTED.** tg_bot.py, manual_flow.py, schedule_config.py; callback tests. |
| 6.7 | Управление AI. | **IMPLEMENTED.** tg_bot.py, manual_flow.py, schedule_config.py; callback tests. |
| 6.8 | Просмотр статистики. | **IMPLEMENTED.** tg_bot.py, manual_flow.py, schedule_config.py; callback tests. |
| 6.9 | Просмотр ошибок и состояния сервисов. | **IMPLEMENTED.** tg_bot.py, manual_flow.py, schedule_config.py; callback tests. |
| 6.10 | Управление расписанием. | **IMPLEMENTED.** tg_bot.py, manual_flow.py, schedule_config.py; callback tests. |
| 6.11 | Кнопки «Назад», «Отмена», «Обновить». | **IMPLEMENTED.** tg_bot.py, manual_flow.py, schedule_config.py; callback tests. |
| 6.12 | Подтверждение действий, запускающих массовые отклики. | **IMPLEMENTED.** tg_bot.py, manual_flow.py, schedule_config.py; callback tests. |
| 6.13 | Корректная обработка устаревших кнопок и повторных нажатий. | **IMPLEMENTED.** Menu callbacks have expiring nonces, pinned profiles and single-use action tokens; stale/replayed actions are rejected. |
| 6.14 | Поддержка цветных кнопок там, где это позволяет Telegram API. | **IMPLEMENTED.** tg_bot.py, manual_flow.py, schedule_config.py; callback tests. |
| 6.15 | Понятные сообщения об ошибках без технических трассировок. | **IMPLEMENTED.** tg_bot.py, manual_flow.py, schedule_config.py; callback tests. |

## 7. AI-функциональность

| ID | Requirement | Status and evidence |
|---|---|---|
| 7.1 | Настройка API-ключа, модели и адреса API. | **IMPLEMENTED.** hh_cli.py, run-apply.py, manual_flow.py; openai.* configuration. |
| 7.2 | Поддержка совместимых с OpenAI API провайдеров. | **LIVE CHECK REQUIRED.** hh_cli.py, run-apply.py, manual_flow.py; openai.* configuration. |
| 7.3 | Генерация сопроводительных писем. | **LIVE CHECK REQUIRED.** hh_cli.py, run-apply.py, manual_flow.py; openai.* configuration. |
| 7.4 | AI-фильтрация вакансий. | **LIVE CHECK REQUIRED.** hh_cli.py, run-apply.py, manual_flow.py; openai.* configuration. |
| 7.5 | Настройка системных промптов. | **IMPLEMENTED.** hh_cli.py, run-apply.py, manual_flow.py; openai.* configuration. |
| 7.6 | Использование данных резюме без выдумывания опыта. | **PARTIAL.** Resume context is supplied, but model factual accuracy requires human review. |
| 7.7 | Обработка ошибок AI API. | **LIVE CHECK REQUIRED.** hh_cli.py, run-apply.py, manual_flow.py; openai.* configuration. |
| 7.8 | Ограничение числа запросов и повторов. | **IMPLEMENTED.** hh_cli.py, run-apply.py, manual_flow.py; openai.* configuration. |
| 7.9 | Возможность отключить AI. | **IMPLEMENTED.** hh_cli.py, run-apply.py, manual_flow.py; openai.* configuration. |
| 7.10 | Сохранение совместимости с настройками ядра 2.5.4. | **IMPLEMENTED.** hh_cli.py, run-apply.py, manual_flow.py; openai.* configuration. |

## 8. Мониторинг и статистика

| ID | Requirement | Status and evidence |
|---|---|---|
| 8.1 | Считать количество найденных вакансий. | **IMPLEMENTED.** ObservedApplyOperation counts candidates yielded by upstream search, including candidates fetched before a limit stops processing. |
| 8.2 | Считать успешные отклики. | **IMPLEMENTED.** run_history.py, _summary.py, tg_watcher.py; persistent history/position tests. |
| 8.3 | Считать пропущенные вакансии. | **IMPLEMENTED.** ObservedApplyOperation counts actual upstream decisions, including silent skips, and returns structured CORE_METRICS. |
| 8.4 | Считать ошибки. | **IMPLEMENTED.** run_history.py, _summary.py, tg_watcher.py; persistent history/position tests. |
| 8.5 | Показывать статистику по аккаунтам. | **IMPLEMENTED.** run_history.py, _summary.py, tg_watcher.py; persistent history/position tests. |
| 8.6 | Показывать историю запусков. | **IMPLEMENTED.** run_history.py, _summary.py, tg_watcher.py; persistent history/position tests. |
| 8.7 | Отображать причины остановки. | **IMPLEMENTED.** run_history.py, _summary.py, tg_watcher.py; persistent history/position tests. |
| 8.8 | Сохранять состояние после перезапуска контейнеров. | **IMPLEMENTED.** run_history.py, _summary.py, tg_watcher.py; persistent history/position tests. |
| 8.9 | Отправлять уведомления о существенных событиях. | **IMPLEMENTED.** run_history.py, _summary.py, tg_watcher.py; persistent history/position tests. |
| 8.10 | Обеспечить корректную работу `tg_watcher.py`. | **IMPLEMENTED.** run_history.py, _summary.py, tg_watcher.py; persistent history/position tests. |

## 9. Docker и развёртывание

| ID | Requirement | Status and evidence |
|---|---|---|
| 9.1 | Актуальный Dockerfile. | **IMPLEMENTED.** Dockerfile, docker-compose.yml, entrypoint.sh, cron_job.py, .env.example, migration guide. |
| 9.2 | Рабочий `docker-compose.yml`. | **IMPLEMENTED.** Dockerfile, docker-compose.yml, entrypoint.sh, cron_job.py, .env.example, migration guide. |
| 9.3 | Отдельные сервисы для основного приложения, Telegram-бота и watcher. | **IMPLEMENTED.** Dockerfile, docker-compose.yml, entrypoint.sh, cron_job.py, .env.example, migration guide. |
| 9.4 | Постоянное хранение конфигурации и состояния. | **IMPLEMENTED.** Dockerfile, docker-compose.yml, entrypoint.sh, cron_job.py, .env.example, migration guide. |
| 9.5 | Корректная передача переменных окружения. | **IMPLEMENTED.** Dockerfile, docker-compose.yml, entrypoint.sh, cron_job.py, .env.example, migration guide. |
| 9.6 | Пример `.env.example` без секретов. | **IMPLEMENTED.** Dockerfile, docker-compose.yml, entrypoint.sh, cron_job.py, .env.example, migration guide. |
| 9.7 | Автоматический перезапуск сервисов. | **IMPLEMENTED.** Dockerfile, docker-compose.yml, entrypoint.sh, cron_job.py, .env.example, migration guide. |
| 9.8 | Healthcheck для необходимых компонентов. | **IMPLEMENTED.** Dockerfile, docker-compose.yml, entrypoint.sh, cron_job.py, .env.example, migration guide. |
| 9.9 | Корректная обработка остановки контейнеров. | **LIVE CHECK REQUIRED.** Dockerfile, docker-compose.yml, entrypoint.sh, cron_job.py, .env.example, migration guide. |
| 9.10 | Логи без чувствительных данных. | **IMPLEMENTED.** Dockerfile, docker-compose.yml, entrypoint.sh, cron_job.py, .env.example, migration guide. |
| 9.11 | Документированное обновление с предыдущей версии. | **IMPLEMENTED.** Dockerfile, docker-compose.yml, entrypoint.sh, cron_job.py, .env.example, migration guide. |
| 9.12 | Возможность отката с резервной копии. | **LIVE CHECK REQUIRED.** Dockerfile, docker-compose.yml, entrypoint.sh, cron_job.py, .env.example, migration guide. |

## 10. Тестирование

| ID | Requirement | Status and evidence |
|---|---|---|
| 10.1 | Unit-тесты обработки HH API. | **IMPLEMENTED.** tests/test_core.py, .github/workflows/ci.yml; no live credentials used. |
| 10.2 | Тесты формирования CLI-команд. | **IMPLEMENTED.** tests/test_core.py, .github/workflows/ci.yml; no live credentials used. |
| 10.3 | Тесты миграции конфигурации. | **IMPLEMENTED.** tests/test_core.py, .github/workflows/ci.yml; no live credentials used. |
| 10.4 | Тесты ручных откликов. | **IMPLEMENTED.** tests/test_core.py, .github/workflows/ci.yml; no live credentials used. |
| 10.5 | Тесты автоматических откликов с имитацией API. | **IMPLEMENTED.** tests/test_core.py, .github/workflows/ci.yml; no live credentials used. |
| 10.6 | Тесты обработки CAPTCHA и ограничений. | **IMPLEMENTED.** tests/test_core.py, .github/workflows/ci.yml; no live credentials used. |
| 10.7 | Тесты Telegram callback-кнопок. | **IMPLEMENTED.** tests/test_core.py, .github/workflows/ci.yml; no live credentials used. |
| 10.8 | Тесты нескольких аккаунтов. | **IMPLEMENTED.** tests/test_core.py, .github/workflows/ci.yml; no live credentials used. |
| 10.9 | Тесты восстановления после ошибок. | **IMPLEMENTED.** tests/test_core.py, .github/workflows/ci.yml; no live credentials used. |
| 10.10 | Проверка синтаксиса Python. | **IMPLEMENTED.** tests/test_core.py, .github/workflows/ci.yml; no live credentials used. |
| 10.11 | Проверка сборки Docker. | **PASSED IN CI.** tests/test_core.py, .github/workflows/ci.yml; no live credentials used. |
| 10.12 | Проверка запуска всех контейнеров. | **PASSED IN CI.** tests/test_core.py, .github/workflows/ci.yml; no live credentials used. |
| 10.13 | Интеграционные проверки с HH.ru на разрешённых тестовых сценариях. | **LIVE CHECK REQUIRED.** tests/test_core.py, .github/workflows/ci.yml; no live credentials used. |
| 10.14 | Проверка отсутствия секретов в релизе. | **IMPLEMENTED.** tests/test_core.py, .github/workflows/ci.yml; no live credentials used. |

## 11. Очистка и качество проекта

| ID | Requirement | Status and evidence |
|---|---|---|
| 11.1 | Удалить неиспользуемые скрипты и патчи. | **IMPLEMENTED.** Separate API, configuration, flow, storage and security modules; bounded request retries. |
| 11.2 | Удалить устаревшие зависимости. | **IMPLEMENTED.** Separate API, configuration, flow, storage and security modules; bounded request retries. |
| 11.3 | Исключить дублирующую бизнес-логику. | **IMPLEMENTED.** Separate API, configuration, flow, storage and security modules; bounded request retries. |
| 11.4 | Разделить API, конфигурацию, Telegram-интерфейс и логику откликов. | **IMPLEMENTED.** Separate API, configuration, flow, storage and security modules; bounded request retries. |
| 11.5 | Добавить единообразную обработку исключений. | **IMPLEMENTED.** Separate API, configuration, flow, storage and security modules; bounded request retries. |
| 11.6 | Добавить таймауты сетевых запросов. | **IMPLEMENTED.** Separate API, configuration, flow, storage and security modules; bounded request retries. |
| 11.7 | Проверить отсутствие бесконечных циклов и неконтролируемых повторов. | **IMPLEMENTED.** Separate API, configuration, flow, storage and security modules; bounded request retries. |
| 11.8 | Сохранить обратную совместимость там, где это необходимо. | **IMPLEMENTED.** Separate API, configuration, flow, storage and security modules; bounded request retries. |

## 12. GitHub и документация

| ID | Requirement | Status and evidence |
|---|---|---|
| 12.1 | Опубликовать исходники в `rorythebreaker/hh-resume-bot-tg`. | **PUBLISHED / CI PASSED.** README.md, INSTALL.md, MIGRATION_2_5.md, CHANGELOG.md, CI, RELEASE.md. |
| 12.2 | Не ограничиваться загрузкой ZIP в репозиторий. | **PUBLISHED / CI PASSED.** README.md, INSTALL.md, MIGRATION_2_5.md, CHANGELOG.md, CI, RELEASE.md. |
| 12.3 | Обновить README. | **IMPLEMENTED.** README.md, INSTALL.md, MIGRATION_2_5.md, CHANGELOG.md, CI, RELEASE.md. |
| 12.4 | Подготовить инструкцию установки. | **IMPLEMENTED.** README.md, INSTALL.md, MIGRATION_2_5.md, CHANGELOG.md, CI, RELEASE.md. |
| 12.5 | Описать миграцию со старой версии. | **IMPLEMENTED.** README.md, INSTALL.md, MIGRATION_2_5.md, CHANGELOG.md, CI, RELEASE.md. |
| 12.6 | Описать переменные окружения. | **IMPLEMENTED.** README.md, INSTALL.md, MIGRATION_2_5.md, CHANGELOG.md, CI, RELEASE.md. |
| 12.7 | Добавить CHANGELOG. | **IMPLEMENTED.** README.md, INSTALL.md, MIGRATION_2_5.md, CHANGELOG.md, CI, RELEASE.md. |
| 12.8 | Настроить GitHub Actions для тестирования. | **IMPLEMENTED.** README.md, INSTALL.md, MIGRATION_2_5.md, CHANGELOG.md, CI, RELEASE.md. |
| 12.9 | Проверить успешность CI. | **PUBLISHED / CI PASSED.** README.md, INSTALL.md, MIGRATION_2_5.md, CHANGELOG.md, CI, RELEASE.md. |
| 12.10 | Создать версионированный релиз. | **PUBLISHED / CI PASSED.** README.md, INSTALL.md, MIGRATION_2_5.md, CHANGELOG.md, CI, RELEASE.md. |
| 12.11 | Приложить готовый архив исходников. | **PUBLISHED / CI PASSED.** README.md, INSTALL.md, MIGRATION_2_5.md, CHANGELOG.md, CI, RELEASE.md. |

## Acceptance

Full compliance is not asserted. Remaining live acceptance: HH login, CAPTCHA, AI provider, one authorized controlled application, and rollback with current tokens. Partial items above require further implementation or acceptance of the documented limitation. Automated tests send no real responses or Telegram messages.

## Publication evidence

Source commit 74b1d2f passed all CI steps, including Python 3.12 tests, source scan, Docker build and healthy startup of all three services against a local Telegram stub. The final documentation commit is also checked by CI. The versioned prerelease includes a git-generated source ZIP; no runtime credentials or data are included. Full live HH acceptance is pending, so the release is explicitly marked prerelease.

Statistics refinement: candidate, sent, skipped and error counts come from an instrumented upstream subclass rather than log heuristics. Upstream source is unmodified. Sending counters include successful applications with tests.
