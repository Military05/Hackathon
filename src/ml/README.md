# B2: обученная модель движения

Реализованы генератор независимых эпизодов, единый movement-v1 extractor, обучение
StandardScaler → MLPClassifier(16,8), выбор порога на validation, baseline,
inference и экспорт независимого D4. Результаты: [ML_REPORT](../../docs/ML_REPORT.md).

    python -m src.ml.generate --seed 42 --test-seed 20261007 --output data/generated
    python -m src.ml.train --input data/generated --output artifacts/local
    python -m src.ml.evaluate --input data/generated --model artifacts/local/movement.joblib
    python -m src.ml.demo --input data/generated --model artifacts/local/movement.joblib

Для готового артефакта обучение не требуется. MovementModel загружается один раз.
WindowEvaluator публикует окна на UTC сетке 5 секунд; fresh=False запрещает повторный
lifecycle. Backend сохраняет/восстанавливает watermark и AnomalyState. Два достаточных
normal окна завершают условие; insufficient_data не normal. Dismiss сохраняет raw
observation и подавляет повтор внутри эпизода до восстановления.

Веса/данные исключены из Git согласно инструкции проекта. Перенос и подключение:
[AI_INTEGRATION](../../docs/AI_INTEGRATION.md). Оценка unusual movement на синтетике
не устанавливает качество настоящего предприятия или вероятность столкновения.
