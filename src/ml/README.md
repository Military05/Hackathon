# src/ml

Реализация и обученные веса поставлены. Актуальный генератор карты v6:
`python -m src.ml.factory_dataset`. Комплект: `models/movement-v1`;
точный dataset: `datasets/factory-v6`. [Отчёт и команды](../../docs/ML_REPORT.md).
Исторические планы ниже не заменяют текущий контракт и авторизацию v5/v6.

## Обновлённая цель v2

Гриша: D4 movement-v1, Pipeline/порог/версии/hash и независимые метрики; перенос готового артефакта на ноутбук. Три профиля видят общий ModelObservation. Не прогноз столкновения. ../../docs/CONTRACTS.md.

Если слово незнакомо, открой [словарь простыми словами](../../docs/GLOSSARY.md). Команды и названия полей не переводим: в коде они должны остаться точными.

Гриша (B2): общий movement-v1 extractor (функция вычисления признаков) для offline/online, episode split (разделение данных), Pipeline scaler+MLP (небольшая многослойная нейросеть)(16,8), train/evaluate/inference. Отчёт docs/ML_REPORT.md. Не обучать в HTTP (протокол обмена с сервером) handler (обработчик запроса). Артефакты artifacts/local не в Git.

Offline train/evaluate и online inference используют один movement-v1 extractor.

## Уточнения аудита

[PREBUILD_FIXES](../../docs/PREBUILD_FIXES.md) задаёт обязательные решения A1–A12/T28–T39. Реализацию отличать от frontend mock; согласованные поля и операции не менять отдельно от владельцев контракта.
