# Фактический отчёт movement-v1 — 2026-10-07

Собственная MLP обучена и сохранена. Эксперимент синтетический, выполнен в среде Codex;
качество настоящего предприятия и target laptop ещё не установлены.
Методика B2/A11: [источники требований](AI_BRANCH_REVIEW.md).

| Параметр | Фактическое значение |
| --- | --- |
| Python / NumPy / scikit-learn / joblib | 3.12.14 / 2.3.5 / 1.8.0 / 1.5.3 |
| Train / validation / test эпизодов | 192 / 64 / 64; в каждой части поровну normal/anomaly |
| Достаточных однородных окон | 2400 / 800 / 800 |
| Train/validation seed; независимый test seed | 42; 20261007 |
| Split | Целые episode_id, без пересечения частей |
| Extractor | src/ml/features.py, movement-v1, 7 признаков по контракту |
| Окно / шаг / минимум | 10 с / 5 с / 6 уникальных timestamps |
| Pipeline | StandardScaler → MLPClassifier(16,8), random_state=42 |
| Scaler / threshold fit | Только train / только validation |
| max_iter / фактические iterations | 600 / 83, early_stopping=False |
| Предупреждения обучения | Нет |
| MLP threshold | 0.5576280463791088; лучший validation F1, tie → precision → больший порог |
| Baseline | speed_std + 0.25·stop_start_count + 0.5·mean_direction_change |
| Baseline threshold | 1.6576603808291348, та же validation |
| Model version / classes | movement-mlp-v1-724859bc62cc / [0,1], anomaly=1 |
| Training time | 0.173 с fit, без генерации/экстракции |
| Extractor + single-window inference | 104 замера: median 0.477 мс, p95 2.600 мс, max 2.767 мс |
| Машина | Codex x86_64, 9 logical CPUs; не целевой ноутбук |
| Model episode recovery | 2 последовательных достаточных normal окна |

Сохранены artifacts/local/movement.joblib, movement.metadata.json, evaluation.json;
data/generated/episodes.jsonl и manifest.json. Веса/данные не в Git согласно инструкции.
Content hash всех src/ml/*.py фиксирует точную реализацию; Git commit — в истории выбранной ветки.

- Dataset SHA256: 724859bc62cc1d08dfa6d7412e8551e09ac764aefb1f5645405c5e3e8ad2ceda.
- Artifact SHA256: 24b808dd0a513fc701862df96bbcd5fff9df6e048ad40d7e5ed3ae96cec351a7.
- ML code SHA256: 211a562a25f66eab1b38a52b4351e8b991e01d78e1c9945abf524eb8ca3341e7.

## Независимый test

| Модель | TP | FP | TN | FN | Precision | Recall | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| MLP | 287 | 5 | 507 | 1 | 0.982877 | 0.996528 | 0.989655 |
| Baseline | 274 | 9 | 503 | 14 | 0.968198 | 0.951389 | 0.959720 |

Confusion MLP [[507,5],[1,287]], baseline [[503,9],[14,274]]: строки/столбцы normal, anomaly.
64 test эпизода, 32 с аномалией, 800 окон (512 normal/288 anomaly).
MLP лучше данного baseline в этом эксперименте; другие генераторы не проверены.

| Оценка целых эпизодов | MLP | Baseline |
| --- | --- | --- |
| Найдено anomaly эпизодов | 32/32 | 32/32 |
| Пропущено anomaly эпизодов | 0 | 0 |
| Эпизодов с ложным сигналом | 3 | 7 |
| Ложных модельных происшествий | 3 | 7 |
| Onset → первое positive однородное окно, median / max | 10 / 10 с | 10 / 30 с |

Ложное происшествие считается при открытии эпизода, восстановление после двух normal окон;
включены ложные сигналы до onset внутри anomaly эпизодов. Окно и эпизод — разные единицы.
Latency — event-time с накоплением окна, без received→incident→UI. Смешанные переходные
окна до накопления 10 с аномалии исключены из метрик по фиксированному ground truth правилу.
Test не использован для обучения/порога, повтор fit был только проверкой воспроизводимости.

## Данные, ошибки, ограничения

Normal: transit/loading/waiting/turns, forklift/truck/cart с разным темпом; ожидание/погрузка
не становятся аномалией по метке сценария. Anomaly: частые нерегулярные изменения движения,
независимо от правил зоны. Маршрут по проездам main frontend, вне зданий. Test noise
0.002–0.015, sampling 0.6–1.3 с; train/validation 0.003–0.010, 0.8–1.05 с. ID/labels/scenario
и готовые нарушения не в features. Timestamp timezone обязателен, ties разрешаются стабильным ID.

Остаются 5 ложных positive окон в 3 эпизодах и 1 пропущенное anomaly окно. Высокий F1 зависит
от синтетического определения аномалии и семейства маршрутов. Score не калиброван как
вероятность аварии. Реальные данные, новая площадка, иной шум/частота и нагрузка с Qwen
требуют отдельной проверки без подбора по данному test.

## Повторение и передача

    python -m src.ml.generate --seed 42 --test-seed 20261007 --output data/generated
    python -m src.ml.train --input data/generated --output artifacts/local
    python -m src.ml.evaluate --input data/generated --model artifacts/local/movement.joblib
    python -m src.ml.demo --input data/generated --model artifacts/local/movement.joblib
    python -m pytest tests/ml tests/agent tests/qa -q

Ожидаются тот же dataset hash, порог/confusion; время зависит от машины. 63 теста прошли,
включая extractor, train-only scaler, split, hash, late windows, lifecycle/suppression
и реальную MLP → agent evidence с language test double. Живой Qwen, полный D1–D10 через A1
и независимый прогон Лией на ноутбуке ещё не выполнены.
[Подключение и перенос](AI_INTEGRATION.md).
