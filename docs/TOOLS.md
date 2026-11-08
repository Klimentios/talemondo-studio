# Инструменты и процессы

Все команды запускаются с Mac из корня репозитория. Хосты, порты и снимки берутся из [STATE.md](STATE.md).
Логи сохраняются в `logs/` (папка не в git): `… 2>&1 | tee logs/<дата>-<тема>.log`.

## Ежедневное
| Задача | Команда |
|---|---|
| Проверка перед коммитом | Точечные тесты затронутых компонентов по реальному риску; `bash scripts/check.sh` только перед релизом/деплоем, после широкого изменения контракта или для конкретной регрессии |
| ReShot (локальные карты движения) | `bash scripts/install_reshot.sh` ставит закреплённый Apache-2.0 ReShot в проектную `.venv`; в Библиотеке выбери видео до 15 с → «Подготовить карту движения» → глубина/позы/контуры. Новый файл затем проверь и добавь в сцену вручную; H3-рендер эта операция не запускает. |
| Перезапуск Mac Studio (только по просьбе, после освобождения очереди) | «Перезапустить Studio.command» проверяет очередь, перечитывает LaunchAgent через `bootout`/`bootstrap` и сверяет загруженные пути с plist. |
| Бэкап данных Studio | `python3 scripts/backup_mac_studio.py` ([guides/STUDIO_BACKUP.md](guides/STUDIO_BACKUP.md)) |

## GPU-сервер
| Задача | Команда |
|---|---|
| **Новый сервер из R2** | `bash infra/h3_up.sh --host root@HOST --port PORT [--stamp STAMP] [--from root@OLD:PORT] [--skip-base]` |
| **Новый сервер Nebius (обычная VM)** | сначала `bash infra/h3_vm_prep.sh --host user@IP` (root по ключу, supervisor, ffmpeg, CUDA 12.8 рядом с 13.0 без трогания драйвера), затем `bash infra/h3_up.sh --host root@IP --port 22 --identity ~/.ssh/id_ed25519`. Диск Nebius пишет ~110 МБ/с: загрузка ~25 мин |
| Только overlay на готовую базу | `bash scripts/h3_new_server.sh --host … --port … [--qwen sleep\|guard] --skip-base` |
| Вся связка без рендера | `bash scripts/verify_h3_server.sh --host … --port …`; рендер только после «ИТОГ: всё OK» |
| Сверка сервера с репо | `bash scripts/h3_server_drift.sh --host … --port …`; «ПОТЕРЯ» означает правку, сделанную только на сервере |
| Эталон пакетов и весов | `bash scripts/h3_server_lock.sh --host … --port …` → `deploy/live-lock/` |
| Бэкап и восстановление конфигов | `bash scripts/h3_server_backup.sh` → `~/H3-Backups/`; `scripts/h3_server_restore.sh --backup DIR --host --port [--apply]` |
| Серверная очередь | `scripts/install_h3_server_queue.sh`; описание: [reference/H3_SERVER_QUEUE.md](reference/H3_SERVER_QUEUE.md) |
| Профиль сценариста | на сервере: `bash /workspace/h3/scripts/h3_scenarist_switch.sh --profile default` (27B удалён) |

Что ставится и известные ловушки: [reference/H3_SERVER_FROM_ZERO.md](reference/H3_SERVER_FROM_ZERO.md).

## Снимки R2
| Задача | Команда |
|---|---|
| Выгрузить новый снимок с сервера | `bash infra/h3_r2_push.sh --host … --port …`; затем прочитать `LATEST`, сверить SHA и записать в STATE.md |
| Удалить всё, кроме LATEST | `bash infra/h3_r2_prune.sh`; только после проверки `LATEST` |
| Снимок софта на Mac | `bash infra/h3_snapshot_pull.sh --host … --port …` |
| Исключить веса из снимков | `infra/h3_snapshot_spec.sh` (`H3_BANNED_WEIGHTS_RE`) |

Устройство снимков описано в [reference/H3_GOLDEN_IMAGE_PLAN.md](reference/H3_GOLDEN_IMAGE_PLAN.md). Ключи хранятся в Keychain: `h3-r2-*`, `h3-hf-token`.

## Проверка без GPU
- Нативный ComfyUI на Mac: `~/H3-ComfyUI-mac` на порту :38188 (launch-конфиг «h3-comfyui-mac»). Это узлы и конфиги без весов.
- Сверка графов: `python3 scripts/h3_graph_validate.py --object-info http://127.0.0.1:38188/object_info tests/golden/*_graph.json tests/golden/*_gpu_*.json`.
- Motion Director на этом стенде: `H3_ROOT=~/H3-ComfyUI-mac H3_COMFY_URL=http://127.0.0.1:38188 bash scripts/install_h3_motion_director.sh --check` (установлен 31.10.2026 тем же пином и патчем, что на GPU). Граф сцены сверяется тем же `h3_graph_validate.py`.
- Docker-стенд: `bash infra/h3_local.sh up|start|check` (Colima `h3`). Удалён 2026-10-13, пересборка займёт около 70 ГБ.

## A/B
- `bash scripts/h3_ab.sh [--sparse]` / `python3 scripts/h3_ab.py …`. Результат: `logs/ab-<дата>/index.html` с синхронными плеерами.
- Сценарист: `python3 scripts/h3_scenarist_ab.py`, результат в `logs/scenarist-ab/`.
- Правила описаны в [guides/H3_AB_HARNESS.md](guides/H3_AB_HARNESS.md): одна переменная, одинаковый вход. Turbo-LoRA в A/B не участвуют.

## Мозг Studio (Claude Code / Codex по подписке)
- Вспомогательные сценарист/планировщик/Context-IR: `H3_BRAIN_PROVIDER=claude|codex`, необязательные `H3_BRAIN_MODEL` и `H3_BRAIN_EFFORT` (`low|medium|high`; Codex также `xhigh|max`). Переменные читаются процессом Studio; при настройке службы применяются после разрешённого перезапуска. Без переопределения Claude использует `opus`, Codex — свой CLI default; глубина не снижается автоматически. Эти настройки не меняют модель существующего чата агента.
- Помощники получают готовый текст и фото в отдельной временной папке (`read_repo=false`); Claude не загружает сторонние MCP (`--strict-mcp-config`). Основной чат продолжает работать через Studio MCP. Семофор вспомогательных вызовов — 2; лимит активных чатов — 4. Это ограничения процессов, не гарантия бюджета токенов. Не измеренная экономия токенов не выдаётся за результат.
- Статус входа: `curl -s localhost:18187/api/brain/status`. Вход: `claude auth login` и `codex login` (Codex лежит в `/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex`).
- Вопрос: `POST /api/brain/ask` `{"provider":"codex"|"claude","prompt":"…","schema":{JSON Schema}}` → `{text, data, seconds}`. Агент только читает репозиторий (docs, skills), не правит файлы; ключи API в окружение не передаются.
- Чужие схемы входа по подписке (как в Ponder) не используем: только официальные CLI.

## Монтажный движок
- «Монтаж» снова в навигации Studio (главный экран чата → «Монтаж», `/editor/` — FreeCut; JOURNAL §524). Прежняя запись: страница «Монтаж» выводилась из навигации Studio: пользователь работает со сборкой фильма и экспортом в Premiere/DaVinci на холсте. Старая страница и данные сохранены для уже созданных проектов.
- «Весь фильм» → «Монтажный лист»: на каждом шве переход, вид (60 xfade), акцент (тряска/вспышка/глитч/цвет), стоп-кадр; переключатели «Звуковой дизайн», «Громкость −14 LUFS», «Плёнка». Титр на шоте: «Титр героя (на стоп-кадре)», «Кинетический заголовок», «Плашка с именем», «Титульная карточка». Цвет: «Цвет» (лук), «Сила лука», «Выровнять цвет шотов», на шоте «Яркость»; лук франшизы по умолчанию — в паспорте («Цвет франшизы»). Экспорт: «Экспорт в Premiere / DaVinci» → «Открыть в DaVinci Resolve» (нужен запущенный Resolve и Preferences → System → General → External scripting using: Local), «Показать папку», «Скачать ZIP». Моушен-графика: `tools/motion` (Remotion 4.0.535, `npm ci` там же), рендер через установленный Google Chrome (`STUDIO_CHROME` — другой путь). API: `GET /api/edl/toolkit`, `POST /api/edl/plan`, `POST /api/edl/renders`; встроенные звуки кешируются в `<данные Studio>/sfx_bank/v1/`. Подробно: `docs/reference/EDIT_ENGINE.md`.

## Агент управляет холстом
- `studio_playbook` возвращает уровни маршрута (`actions`) с именами инструментов и правилами экономии токенов: рутинные проверки выполняет Studio, `run_delegate` используется для узкого read-only суждения помощника. Контракт подключения новых моделей — `docs/reference/STUDIO_AGENT_ARCHITECTURE.md`.
- `studio_readiness` одним read-only вызовом показывает блокеры GPU/очереди, число заданий по статусам и ID неоднозначных квитанций; не запускает и не возобновляет задания.
- `run_ask(gate="gpu", gpu_uses=N)` выдаёт после «Да» только N действий агента с GPU; каждый вызов рендера, ключевого кадра или сборки сцены расходует одно. Сборка сцены может создать несколько GPU-заданий, поэтому в вопросе перечисляются шоты и ключевые кадры. Если проверка готового шота недоступна, автосборка останавливается до QC.
- Открытая вкладка Studio — «руки» агента (`preview/h3-director/ui-bridge.mjs` ↔ `local_studio/ui_bridge.py`, `/api/ui/*`): MCP `studio_board` (что на холсте, job_id шотов), `studio_apply_scene` (сцена на холст без клика, проверки как у `scene_draft`), `studio_build_scene` («Собрать сцену» тем же кодом, что кнопка: ключевые кадры, цепочка с последнего кадра; в задаче — после «Да» на gate=gpu), `studio_stop_build`, `studio_reset_scene`, `studio_select_scene`, `studio_new_scene`. Без открытой вкладки инструмент так и отвечает. В задаче `run_wait(scene_ids=[…])` будит агента, когда сцена собрана (по черновику проекта).
- `studio_apply_scene` возвращает фактический `board` и `verification`; при расхождении агент сверяет холст, не повторяет применение вслепую. Потерянное подтверждение команды с побочным действием помечается `needs_reconciliation`; другая вкладка не выполняет её второй раз.
- Кнопка ↺ «Сбросить сцену» на холсте: один пустой шот, без материалов, стиля, раскадровки и плана; ролики остаются в очереди и библиотеке. Корзина на единственной сцене предлагает то же.

## Задачи агента (оркестратор)
- Раздел «Агент» (`/agent-chat.html`), переключатель «Задача — агент ведёт сам до результата»: цель → агент (Claude/Codex по подписке, режим «действует») сам составляет план и ведёт его по шагам через MCP `run_plan`, `run_step`, `run_ask`, `run_wait`, `run_delegate`, `run_done`. Перед тратой GPU — вопрос «Да/Нет» (`gate=gpu`): без «Да» `render_enqueue` и `keyframe_create` в задаче отказывают (MCP получает `H3_RUN_ID`, проверяет `GET /api/runs/{id}/gate`). Studio будит агента, когда ты ответил, когда готовы рендеры/кадры/сборки (опрос раз в 15 с) или по «Продолжить». Помощники — короткие чаты «только смотреть» с одной ролью. API: `/api/runs`, данные — `<данные Studio>/runs/`. Код: `local_studio/orchestrator.py`.

## Чат с агентом в Студии
- `/agent-chat.html` («Чат с агентом» в меню): Codex или Claude Code по подписке, выбор модели, режим «Только смотреть» (запись через MCP запрещена) или «Может действовать» (Codex: `default_tools_approval_mode="auto"` только для этого запуска; Claude: все инструменты h3-studio). Агент не правит файлы и не запускает команды: Codex в `--sandbox read-only`, у Claude только Read/Glob/Grep.
- «✦ Агент» в форме «Создать видео» (`agent-dock.mjs`): агент видит промпт, референсы и настройки формы и отвечает черновиком `composer_draft` (MCP проверяет файлы и роли, ничего не ставит в очередь); пользователь жмёт «Применить в форму», потом «Создать». Целая сцена: «<франшиза>, сцена 30 с: …» → агент читает паспорт и библию, применяет правила `SCENE_CRAFT` (`local_studio/agent_chat.py`: крючок, событие в каждом шоте, смена крупностей, монтаж по палитре франшизы, продолжение с последнего кадра) и отдаёт `scene_draft`; MCP проверяет правила доски и прогоняет `shot_lint`/линт франшизы, агент исправляет замечания. «Применить на холст» → «Собрать сцену» делает ключевые кадры и рендер. У Claude модель по умолчанию — `opus` явно: глобальная `fable[1m]` из `~/.claude/settings.json` недоступна CLI по подписке.
- API: `GET /api/agent-chat/providers`, `POST /api/agent-chat/chats`, `POST /api/agent-chat/chats/{id}/messages` (поток NDJSON), `GET …/stream` (переподключение), `POST …/stop`. Чаты: `<данные Studio>/agent_chats/`.

## MCP-сервер Studio (Claude Code / Codex управляют Студией из чата)
- `scripts/h3_studio_mcp.py` — stdio, только стандартная библиотека, ходит в API Studio (`H3_STUDIO_URL`, по умолчанию :18187). Схемы записи берёт из живого `/openapi.json`.
- Подключение: `claude mcp add --scope user h3-studio -- python3 "$PWD/scripts/h3_studio_mcp.py"`; `codex mcp add h3-studio -- python3 "$PWD/scripts/h3_studio_mcp.py"` и в `~/.codex/config.toml` у `[mcp_servers.h3-studio]` строка `default_tools_approval_mode = "writes"`.
- Перед кадрами и рендером план шотов проходит `shot_lint` (или `POST /api/shot-lint` `{"shots":[…],"target_seconds":N}`): ошибки исправить, предупреждение оставлять только с полем `intent`.
- Паспорт франшизы: `/franchise-profile.html` или MCP `franchise_profile` / `franchise_profile_update` (с revision). Перед кадрами и рендером шоты франшизы проходят `franchise_resolve_shots` (готовые asset_ids, reference_roles/owners, строки облика, director_config для `render_enqueue`) и `franchise_shot_lint`.
- Монтажный лист в интерфейсе: «Сюжет» → «▣ Весь фильм» открывает панель «Монтаж всего фильма» (`preview/h3-director/edit-sheet*.mjs`): переход на каждом стыке (по умолчанию из карточек шотов), музыка, затемнения, «Проверить монтаж», «Собрать с переходами»; старая склейка без переходов — кнопка внизу панели.
- Монтажный лист (EDL, `local_studio/edl.py`): `edl_from_shots` (шоты с `job_id` → черновик листа), `edl_plan` (таймлайн по реальным MP4 без рендера: стыки, наложения переходов, звуковые мосты, хронометраж, `shot_lint` по карточкам клипов), `edl_render` → `edl_render_status` (сборка MP4 на Mac через FFmpeg, без GPU). HTTP: `POST /api/edl/from-shots|plan|renders`, `GET /api/edl/renders/{id}[/file]`, таблица переходов `GET /api/edl/transitions`. Лист: `{"aspect_ratio":"9:16","clips":[{"job_id","in","out","speed","transition_in","transition_duration","audio","volume","shot"}],"music":{"asset_id","volume","duck"},"sfx":[{"asset_id","at"}],"fade_in","fade_out","target_seconds"}`. Переходы — те же значения, что `shot.transition_in` в схеме: склейки встык, `whip_pan`/`pass_through`/`morph` — короткий мост xfade (сам эффект делает шот), `dissolve`/`fade` — растворение/через чёрное, `sound_bridge` — звук следующего шота входит раньше картинки.
- Запись (рендер, кадры, правка карточек, отмена, склейка, сборка листа) помечена `readOnlyHint: false`; `render_enqueue` требует `story_approved: true`.
- Превиз (Blockout, `~/Applications/Blockout.app` v5.0.0, Apache-2.0, github.com/wassermanproductions/blockout): открой приложение — Studio находит его по `~/.config/blockout/control.json`. MCP `previs_status`, `previs_stage` (его действия: сцена, герои в метрах, метки, камера и объектив), `previs_view` (кадр в аспекте шота), `previs_snapshot` (кадр → библиотека как серая размытая «Композиция»), `previs_motion` (движущаяся камера шота → серое видео «Камера» на всю длину шота, 24 fps). После запуска Blockout один раз нажми в нём New project — API проект не открывает. HTTP `/api/previs/status|call|frame|snapshot|motion` (`local_studio/previs.py`). Кадр рисуется вне экрана 960×540 и режется по аспекту шота; надписи (`label`) рисуются в кадре — не ставить.

## Навыки агентов
Навыки лежат в `skills/` (в git) и подключены симлинками в `~/.codex/skills` и `~/.claude/skills`. После добавления нового навыка запусти `bash skills/link.sh`. Список: [../skills/README.md](../skills/README.md).
Для H3-видео `studio_playbook` сначала предлагает `minimax-video-production`; точный формат IR и реестр принятых замен открываются по указанным в нём условиям. Отдельную панель навыков в Studio это не добавляет.

## Процесс работы агента (кратко, полностью в AGENTS.md)
1. Начать: `git status`, затем прочитать STATE.md, DECISIONS.md и открытые пункты JOURNAL.md.
2. Работать: захватывать файлы через JOURNAL.md. Studio тестировать во временной сцене.
3. Закончить:
   - Точечные тесты затронутого поведения; полный `bash scripts/check.sh` только по условиям выше.
   - коммит
   - строка в CHANGELOG.md
   - если изменился факт — поправить STATE.md


## VideoAgent — исходники агента монтажа

По просьбе пользователя 2026-10-24 скачан [HKUDS/VideoAgent](https://github.com/HKUDS/VideoAgent) в `tools/VideoAgent/` отдельным shallow Git-клоном. Версия: `f207987e3cffb554aaa6ffdbe733efb30f4b51ed` (upstream-коммит 2026-07-22). Папка исключена из Git STUDIO; исходники upstream не изменены.

Проверены чистота клона и `git fsck`. Корневой `LICENSE` — MIT; в `pyproject.toml` есть несогласованное обозначение Apache. Перед интеграцией учитывать лицензии вложенных компонентов отдельно.

Статус: скачан код (~304 МБ), зависимости и веса не установлены, запуск и монтаж не проверены. README указывает Linux/Windows и GPU от 8 ГБ; зависимости включают CUDA-пакеты. Точка входа — `main.py`, конфигурация LLM — `environment/config/config.yml`. Для запуска нужны отдельное окружение, совместимые модели и настройка LLM; платные API не подключены. В текущую Studio код не интегрирован.

### TaleMondo: точечные операции монтажа

MCP `montage_edit` → `POST /api/montages/{id}/edit`: сначала `montage_get`, затем обязательные `revision`, `note` и `ops`. Поддержаны `addClip` (index, clip), `removeClip` (index), `moveClip` (index, to — конечная позиция), `trimClip` (index, source_in/source_out в секундах исходника), `setTransition` (index, transition, seconds). Индексы нулевые, каждая операция видит результат предыдущей. Один пакет сохраняется как одно агентское изменение; ошибка или конфликт 409 не сохраняет пакет. При конфликте нужно прочитать монтаж заново и пересмотреть правки. Открытый редактор использует существующее обновление по revision и отмену агентского изменения. Рендер этот инструмент не запускает.

Идея операций оценена по [VideoSOS 2](https://github.com/timoncool/videosos/tree/78917e6985ae5ac76a4302af7c7bc656970f9a89) (MIT); реализация собственная поверх текущего EDL, код и зависимости VideoSOS не копировались. Полный редактор VideoSOS не встроен. Его облачные провайдеры не подключались. [Floorplan](https://github.com/haydenschmitty/floorplan) (MIT) пригоден как инструмент архитектурной карты, пока не установлен. SOLVED DIRECTOR не идентифицирован: до получения первоисточника интеграция не планируется как подтверждённая возможность.

### FreeCut — замена монтажного интерфейса (в проверке)

Источник: https://github.com/walterlow/freecut, MIT © 2025 FreeCut, закреплён commit `5ceeeb388eef5423640b7db157706274bf1b8165`. Исходный checkout в `tools/freecut-upstream/` игнорируется git; воспроизводимая подготовка: `bash scripts/prepare_freecut.sh`. Адаптер в `tools/freecut-adapter/`; лицензия upstream сохранена в checkout. Запуск: `node tools/freecut-adapter/serve.mjs`, адрес http://127.0.0.1:18189/projects.

Редактор работает в Chromium, требует выбора отдельной папки workspace. Не выбирать папку данных H3 Studio. Нативные сохранение и экспорт FreeCut, панель «TaleMondo · медиатека» использует штатный importMediaFromUrl и источник /api/montages/media. Локальный host проксирует только чтение списка медиа и файлов/постеров; инструменты записи, генерация, настройки и секреты через прокси недоступны. Service worker адаптера выключен для предсказуемого обновления локальной сборки.

Подтверждены установка и production-сборка на Mac, HTTP-раздача и доступность медиатеки. Не подтверждены браузерное открытие workspace, импорт, звук и экспорт MP4: browser use отключён в Work. Поэтому старый редактор ещё не удалён, ссылка на FreeCut добавлена в его шапку. Старый EDL и FreeCut workspace не синхронизированы; существующие montage_* MCP пока управляют старым EDL. До переключения требуется адаптация MCP и решение переноса проектов. Автоматического скачивания AI-моделей или генерации при подготовке нет.

Обновление интеграции: `/editor/` теперь оболочка TaleMondo с iframe локального редактора; прежняя страница доступна для восстановления через `/editor/legacy.html`. Workspace создаётся автоматически в OPFS origin `127.0.0.1:18189` в каталоге `talemondo-workspace`, без выбора папки. Это browser storage, а не общая папка Studio: очистка данных браузера удалит workspace; использовать штатный экспорт проекта для резервной копии. Старые EDL автоматически не импортируются. Требуется продолжить проверку совместимости worker/OPFS, browser codec/export и MCP.

Очистка 02.11: старые `editor/legacy.html`, `montage.mjs`, `montage-model.mjs`, их стили и UI-тесты удалены. Ссылки на legacy выше — история перехода, действующего fallback больше нет. Серверные EDL и их MCP не удалены: они нужны для существующих монтажей и экспорта, отдельно от FreeCut.

### FreeCut: канал открытого редактора (02.11, проверка не завершена)

MCP `freecut_timeline` вызывает локальный `/bridge/command`, а видимая вкладка выполняет штатные `applyOp` и `saveTimeline`. Чтение возвращает project_id/revision; запись требует оба поля и ровно одну операцию. Сервер допускает одного владельца-вкладку с lease, один pending command, проверяет Host/Origin/Content-Type. Отключённая вкладка даёт 503. Ответ pending нельзя повторять как новую запись. Не поддержаны восстановление очереди после падения процесса и миграция EDL; полная пользовательская проверка и undo ещё необходимы.

Ошибка маршрута теперь передаётся в `/bridge/status` после её повторного появления. Диагностика только в памяти localhost host, без внешних сервисов.

## H3 Studio · переносимая связка Studio ↔ ComfyUI без GPU

Проверка контракта на CPU-стенде (только GET, без отправки prompt, весов и медиа):
```bash
.venv/bin/python scripts/verify_studio_engine.py --api-url http://127.0.0.1:28189 --comfy-url http://127.0.0.1:28188
```
Проверяет API/Director schema, загруженные маршруты обмена графами и контекста
выполнения, золотые графы и матрицу настроек, а также компиляцию Motion Director.
Для нового сервера указать адреса его API/ComfyUI через локальный туннель.
Это конфигурационная приёмка, не тест inference/качества.

В распакованном GPU overlay есть `scripts/install_h3_studio_extensions.py`.
`python3 scripts/install_h3_studio_extensions.py --comfy /workspace/h3/ComfyUI`
только сверяет файлы; `--url http://127.0.0.1:18188` также проверяет загруженные
обработчики. `--apply` устанавливает только файлы bridge/observer с резервными
копиями, без моделей, зависимостей, удаления пользовательских файлов и рестарта.
Общий overlay вызывает именно этот установщик. На GPU применение и перезапуск
служб остаются отдельным разрешённым действием согласно AGENTS.md.

Свои узлы и модели пользователя (манифест «Узлы и модели» в Studio, `<данные>/comfy/manifest.json`):
`bash scripts/h3_comfy_manifest_apply.sh --host root@HOST --port PORT --manifest FILE` — план;
с `--apply` ставит закреплённые пакеты и модели с проверкой SHA-256 (`scripts/install_comfy_manifest.py` на сервере)
и перезапускает h3-comfyui только при изменениях и пустой очереди. Кнопка «Применить на GPU» вызывает то же.
Установка Studio на Mac: двойной щелчок «Install Studio.command» в корне (или `bash scripts/install_studio_on_mac.sh`).
