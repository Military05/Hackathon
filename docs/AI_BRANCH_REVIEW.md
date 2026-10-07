# Проверка веток перед B1/B2 — 2026-10-07

Рабочая ветка пользователя: codex/three-dispatcher-plan. Проверены деревья и содержимое
всех шести доступных веток Hackathon, одинаковые blobs сопоставлены по SHA. До этого
изменения agent/ml во всех ветках содержали инструкции без реализации.

| Ветка | Проверенный head | Существенное для AI |
| --- | --- | --- |
| codex/three-dispatcher-plan | 4842ab4f8b85a906a0ae0a154b9ddd740a0ba1f8 | База работы, v2, три профиля, инструкции B1/B2, QA runner |
| main | c45e4ae31b30ce7102b8cfeaf3c2e3a73934ce4f | Frontend mock/API client, PREBUILD_FIXES 2.1-audit |
| codex/audit-interface-integration | 72d72ff1b9185841284a494c5c2600406e3fbad9 | То же файловое дерево, что проверенный main |
| core/interface-skeleton | 8beeccbd99079974f469d3241affcf1040990fed | Ранний интерфейс, без AI реализации |
| docs/enterprise-panels-2026-10-06 | 4517b9735ccc45ab870cdde915143f4814f2041d | Архитектура панелей, ранние требования |
| qa/scenario-report | 5c57cc436cd6526a6f41ef6feef80c777ef12c4d | Сценарии/QA, без live backend/AI |

Применены поздние требования [PREBUILD_FIXES на проверенном main](https://github.com/Military05/Hackathon/blob/c45e4ae31b30ce7102b8cfeaf3c2e3a73934ce4f/docs/PREBUILD_FIXES.md):

- A8/T37: 2 queued вместо устаревших 20, строгая проверка ожидаемого времени.
- A9/T36: materialized as_of snapshot, typed evidence, проверка значения при правильном
  ID; never_started подтверждается сохранённым sensor-health, без вымышленного heartbeat.
- A10/T35: lifecycle helper сохраняет отклонение модельного эпизода и raw observation.
- A11/T38: normal loading/waiting/turns, независимые seed/sampling/noise, baseline,
  ложные эпизоды и latency, проезды вне зданий.
- A12/T39: модульные тесты, HTTP test double, реальный Qwen и общий backend проверяются отдельно.

Dashboard найден в main/audit, src/interface/web: mock и API provider. Это не готовый
общий backend. В выбранную ветку frontend/координация не перенесены: текущая работа
ограничена AI Гаджи/Гриши. Общие документы других владельцев не переписаны; этот документ
фиксирует, почему очередь реализации отличается от старых 20 в CONTRACTS.
