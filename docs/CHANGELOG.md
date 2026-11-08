# Журнал изменений

2026-11-08 · Klimentios · Added the English ComfyUI-to-Studio workflow explanation and a verified scene-board screenshot to README; no runtime code changed.

2026-11-08 · Klimentios · Linked two verified English interface screenshots in the public README; checked the images for private project content.

2026-11-08 · Klimentios · Rewrote the public README entirely in English; checked it for Cyrillic and preserved the product, setup, credit, and licence information.

2026-11-08 · Klimentios · публичная редакция: в README добавлены ссылка на обзор с семью видеопримерами и точные благодарности MiniMax AI, Comfy-Org и Kijai; код и параметры рендера не менялись.

Одна строка на изменение: дата · кто · коммит · что · проверено ли.
2026-11-08 · Claude · установка в один клик: «Установить Studio.command», мастер «Первый запуск» с кнопками, сервер с нуля без R2 и без токенов (открытые веса Hugging Face). Тесты 1328 Python + 406 JS; установщик проверен с нуля и повторно на Mac, установка GPU не проверялась.
2026-11-08 · Claude · правка графов ComfyUI из Studio (из 3b462e02) · общий движок app/graph_ops.py (значения, связи, добавить/удалить/заменить узел, LoRA; проверка по живому /object_info), версии воркфлоу с историей и возвратом, граф задания → свой воркфлоу, архив графов заданий на Mac, редактор в /workflows.html, MCP workflow_graph_edit/versions/restore/from_job/from_recipe, comfy_node_search/class/graph_check · тесты; рендер не проверялся
2026-11-08 · Claude · публичная редакция без чужих франшиз: удалены принятые рецепты владельца и пресеты франшиз, имена в примерах и тестах нейтральные; замена и смена мира по видео без рецептов берут встроенные настройки, рецепты пользователя — в $H3_MAC_DATA_DIR/recipes · pytest + node --test зелёные
2026-11-08 · Claude · публичная версия: английский интерфейс (dom-translate.mjs + словарь i18n/en.json, ~7700 фраз; RU/EN внизу меню, язык по браузеру), лицензия PolyForm Noncommercial 1.0.0 и подпись автора (LICENSE, NOTICE, README, меню), запреты на модели в правке графов сняты (турбо — предупреждение), вёрстка: скрытые плашки, обрезанные списки, шапка и настройки агента на телефоне, запасной показ «Видео студии» · все страницы RU/EN на 1440 и 390 пикселях без переполнений; тесты · рендер не проверялся
2026-11-08 · Claude · публичная версия, полный обход кнопок: доска франшиз без франшиз/досок показывает пустое состояние (а не «Загрузка досок…»), её стили и кнопки — на токенах Studio, доски по умолчанию на языке интерфейса, метки TALEMONDO; аналитика — свой адрес Studio вместо :18208, местный часовой пояс (H3_TIMEZONE) вместо Кипра, число источников по факту; «Монтаж» без FreeCut — подсказка установки; паспорт без франшизы и «Граф задания» с пустым id больше не шлют пустые запросы · все кнопки 14 страниц RU/EN без ошибок; pytest 1334/1 skip, node 409 · рендер не проверялся
