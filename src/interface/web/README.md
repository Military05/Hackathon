# Карта и диспетчерский интерфейс / Егор М.

Текущая страница index.html/app.js/styles.css работает с живым /api сервера.
Leaflet 1.9.4 хранится в vendor/leaflet с лицензией; CRS.Simple, координаты 0..100,
без GPS/тайлов/CDN. Вся сцена читается из одного /api/site.

Три профиля выбираются в UI или ?operator=dispatcher-1/2/3. Polling раз в секунду;
presence каждые 3 секунды. При смене профиля прежняя сессия получает away.
У каждой вкладки свой session_id; ready одной вкладки сохраняет готовность профиля.
Карточка сохраняет input/recipient. PATCH использует видимую dispatch_revision;
409 обновляет карточку и требует нового осознанного действия.

Секторный журнал и общий переключатель, история отклонённых/завершённых,
summary unknown/escalated, claim/response/transfer/reassign/close,
notification cursor/тихая начальная история и звук по разрешению.
Регламент response_plan отображается независимо от ИИ. Готовность reassign
окончательно проверяет сервер по lease/grace/сроку отсутствия.

ML/агент/симулятор этой поставкой не добавляются. unavailable отображается явно,
запуск генератора и анализ недоступны. Транспорт появляется только из Event API,
а не из фиктивной анимации. Последняя stale позиция остаётся с текстом unknown.

Проверки: node --check src/interface/web/app.js;
node --test tests/interface.test.js. Старые dispatch-core.js/provider.js и
тесты tests/interface/*.cjs сохранены как прежний mock/provider prototype,
но текущий index.html их не подключает и успешный mock не подменяет сервер.

Запуск и передача: docs/RUNTIME_QUICKSTART.md и docs/DEVELOPMENT_STAGES.md.
