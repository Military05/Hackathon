> Текущий bundle после аудита: movement-v2. См. [ML_AUDIT_FIX](ML_AUDIT_FIX.md).
> Все результаты ниже относятся к исторической movement-v1; независимая
> приёмка новых весов по указанию координатора пока не запускалась.

# Обучение MLP на актуальной карте v6

Дата: 8 октября 2026. Ветка `codex/mlp-factory-v6`, базовый source commit
`35c9e7b0685a44894681c013c75fdf3199565233`.
Обучение выполнено на Windows 11, Intel Core i5-12500H. MLP работает на CPU;
Qwen3 4B — на NVIDIA RTX 4060 Laptop через Bionic/CUDA.

## Карта, данные и обучение

Прежний генератор использовал одну жёстко заданную ломаную и только V1.
Новый `src/ml/factory_dataset.py` читает актуальный `data/demo/site.json`:
18 зданий с подъездами, 34 дороги, 7 зон, V1/V2/V3, типы транспорта и
зарегистрированные position-сенсоры. В каждом split нормальные эпизоды
покрывают все road IDs и building entrance IDs; catalog хранится в manifest.

Вход остаётся movement-v1: speed_mean, speed_std, idle_ratio, path_length,
max_step_distance, stop_start_count, mean_direction_change. Scenario, ID,
класс и зона не передаются в признаки. Зоны/допуски не являются ML-меткой:
нормальная динамика может нарушать отдельное серверное правило. Сотрудники,
КПП и heartbeat входят в версию карты, но эта MLP обучается только на position.

Нормальные случаи: движение, погрузка, ожидание, повороты, развороты у подъездов.
Аномалии: синтетические рывки, резкие остановки/старты, осцилляции.
Модель оценивает необычную динамику одного объекта, не вероятность ДТП.
Pipeline полностью переобучен на новом наборе, включая scaler, вместо
продолжения старых весов с устаревшей нормализацией. Старый артефакт в истории Git.

| Параметр | Значение |
| --- | --- |
| Python / numpy / sklearn / joblib | 3.12.10 / 2.3.5 / 1.8.0 / 1.5.3 |
| Train / validation / test | 666 / 222 / 222 эпизода, по половине normal/anomaly |
| Seed train / validation / test | 20261008 / 20261009 / 20261010 |
| Train / validation / test окна | 14319 / 4773 / 4773 |
| Окно / шаг / минимум samples | 10 с / 5 с / 6 |
| Pipeline / random_state / max_iter | StandardScaler → MLP(16,8) / 42 / 600 |
| Итерации / convergence warnings | 32 / нет |
| Время model.fit | 0,762 с; подготовка и подбор порога сюда не входят |
| Порог MLP | 0.15122588236235446, только validation |
| Baseline | speed_std + 0.25·stop_start_count + 0.5·mean_direction_change |
| Порог baseline | 2.2376359360611966, только validation |

Split по целым episode IDs выполнен до scaler. Три разных seed; test имеет
более широкий диапазон интервалов измерения и шума. Смешанные окна вокруг
onset исключены. Test не использован для весов, порога или гиперпараметров.
Повторное сохранение после доработки служебного кода дало ту же SHA256 весов;
параметры не менялись. Обучение не выполняется в HTTP-сервере.

Model version: `movement-mlp-v1-6ca8f5089f55`.

SHA256 dataset:
`6ca8f5089f552974e09d309bb245b70e5bfcdefde0ef71c5a2e3482f4e063618`.

SHA256 movement.joblib:
`2505776049711209f4b968f906cf83f2eccf2bb3330359d36a4e1cef5ac74598`.

Семантическая SHA256 карты:
`a12dfeee79dc969c2f6b70baa0cb69b31d3a80e233a2f5994a0140bae12b8a9a`.
Raw site/source хеши записаны в metadata; семантический хеш не зависит от CRLF/LF.

## Независимый test

| Модель | TN | FP | FN | TP | Precision | Recall | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Baseline | 2952 | 45 | 135 | 1641 | 0.97331 | 0.92399 | 0.94801 |
| MLP | 2997 | 0 | 3 | 1773 | 1.00000 | 0.99831 | 0.99915 |

4773 окна из 222 отдельных эпизодов. Обнаружены все 111 аномальных эпизодов,
пропущены три отдельных окна. Ложных модельных эпизодов — 0, у baseline — 35.
На validation у MLP одно ложное и два пропущенных окна; они сохранены в metadata.
Медианная задержка обнаружения — 10 с от onset, включая накопление окна.

Extractor + inference: 172 окна, median 0,655 мс, p95 1,026 мс, max 1,375 мс.
Это локальное одиночное применение на CPU, не трёхклиентский benchmark с Qwen.

## Настоящий DemoRunner v6

Все 13 сценариев проиграны через Service и временную SQLite: 180 секунд
модельного времени на сценарий, 1404 оценки. События реально созданы DemoRunner;
часы ускорены, LLM в этом прогоне не участвует.

| Штатный сценарий | Anomaly окон новой MLP | Anomaly окон старой MLP |
| --- | --- | --- |
| normal | 0 | 11 |
| logistics | 0 | 1 |
| shift | 0 | 24 |
| service | 0 | 8 |
| safe-passing | 0 | 1 |

В этих пяти сценариях нет ни одного нового model_anomaly; старая модель дала
45 положительных окон на тех же событиях. По 105 достаточных окон и 3
insufficient_data на сценарий. Серверные правила зоны/маршрута/сближения/
доступа/связи сохранили свои реальные происшествия независимо от MLP.

В нарушающем service-zone — восемь anomaly окон и один model_anomaly;
результат сохранён, не подавлялся. Отсутствие ложных тревог заявляется для
перечисленных штатных сценариев, а не любого перехода или реального предприятия.
Adapter с чтением watermark: median 2,966 мс, p95 6,558 мс, max 20,056 мс;
ускоренный прогон не является измерением устойчивой wall-clock нагрузки.

Поставлены `models/movement-v1/evaluation.json`, `factory_acceptance.json`,
`training_manifest.json`. Точный набор — `datasets/factory-v6/episodes.jsonl.gz`
(2,95 МБ), распакованные байты проверяются по SHA256. Данные синтетические.

## Интеграция и проверки

Новые веса загружаются из `models/movement-v1`. Проверяются хеш, библиотеки,
feature order и семантическая карта. Изменение site.json даёт artifact_invalid
с объяснением вместо применения устаревшей модели. Watermark учитывает
model_version: после обновления не возвращает прежние веса. История сохранена;
поздние события не переписывают опубликованное окно той же версии.

Живой HTTP → Events → новая MLP → Incident → настоящий Qwen: PASS, 21,98 с.
Первый ответ Qwen округлил порог и был отклонён. Финальная JSON-схема теперь
разрешает точные значения status/score/threshold, прочитанные tools;
сопоставление ID/поля/значения не ослаблено. Локальный отчёт:
`artifacts/local/factory-qwen-backend.json`.

Проверки: runtime 114, QA 18, agent/ML 66, Node 35 + 16 + 20.
Логи: `artifacts/local/factory-*.log`. Первый параллельный QA-прогон имел один
сбой ожидания HTTP-сценария; полный повтор прошёл. Добавлен AI CI с Python 3.12;
его результат не подменяет реальный Qwen/CUDA.

## Повторение

Python 3.12, requirements-ai-dev.txt, из корня проекта:

```powershell
python -m src.ml.factory_dataset --output data/generated/factory-v6
python -m src.ml.train --input data/generated/factory-v6 --output artifacts/local/factory-candidate
python -m src.ml.evaluate --input data/generated/factory-v6 --model artifacts/local/factory-candidate/movement.joblib
python -m src.ml.factory_acceptance --model artifacts/local/factory-candidate/movement.joblib --seconds 180
python -m src.ml.demo --input data/generated/factory-v6 --model models/movement-v1/movement.joblib
python scripts/ai_live_acceptance.py
```

Генератор читает текущую карту; после её изменения SHA256 набора изменится.
Точный исходный набор можно распаковать из опубликованного gzip; manifest рядом.
Кандидат не заменяет работающий сервер автоматически: после проверки нужен
согласованный комплект и перезапуск. Старые оценки в SQLite остаются историей.

Ограничения: синтетические аномалии заметно отличаются от нормы, облегчая
классификацию. Нет real-factory ground truth, промышленной верификации,
полной трёхклиентской нагрузки или remote TLS отчёта. Qwen не дообучалась;
изменены собственная MLP и минимальная интеграция её чисел.
