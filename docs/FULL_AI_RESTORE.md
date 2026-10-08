# Полное восстановление локальных Qwen + MLP (Windows)

Рабочая ветка: 123456768. Репозиторий публичный. НЕ коммитьте .env, пароли, базы SQLite, диагностические журналы и личные настройки. Исходный GGUF не меняется.

## Уже присутствует
- MLP v2 trained weights: models/movement-v1/movement.joblib; metadata и training_manifest рядом.
- Версия MLP: movement-mlp-v2-51ccabc71783; SHA256 joblib из metadata: 1e231a9ab9fa2ff77061e151f2f828c0f6f215841436bcbde9f069aa83f701dd.
- Dataset: datasets/factory-v6/episodes.jsonl.gz и manifest.json.
- Python backend, JS frontend, тесты, скрипты и requirements-ai.txt.

## Как добавить именно вашу Qwen на GitHub
Файл Qwen3-4B-Q4_K_M.gguf имеет размер примерно 2.5 GB. Он пока не в GitHub. Скрипт scripts/qwen_bundle.py разбивает исходный файл на части по 512 MiB и проверяет SHA256, чтобы их можно было загрузить через Git LFS. На бесплатном GitHub максимальный размер одного LFS-объекта 2 GiB. Нужны квоты Git LFS. Прежде чем публиковать веса в открытом репозитории, проверьте право на распространение и лимиты хранилища.

PowerShell в исходном ноутбуке:

~~~powershell
$repo = 'C:\Users\_ADMIN_\Hackathon\.worktrees\all-local-release'
git -C $repo status -sb
git -C $repo pull --ff-only origin 123456768
git lfs install
git -C $repo lfs track
~~~

Найдите локальный файл Qwen3-4B-Q4_K_M.gguf в LM Studio/Bionic. Путь может быть на C: или E:. Ниже подставьте его точный путь:

~~~powershell
$gguf = 'ПОЛНЫЙ_ПУТЬ_К_МОДЕЛИ\Qwen3-4B-Q4_K_M.gguf'
$py = 'C:\Users\_ADMIN_\Hackathon\.venv\Scripts\python.exe'
& $py "$repo\scripts\qwen_bundle.py" pack --source $gguf
& $py "$repo\scripts\qwen_bundle.py" verify
git -C $repo add -- models/qwen3-4b-q4-k-m/manifest.json models/qwen3-4b-q4-k-m/parts
git -C $repo lfs ls-files
git -C $repo status --short
git -C $repo commit -m 'Store exact Qwen GGUF as LFS parts'
git -C $repo push origin 123456768
~~~

Ожидаемый SHA256 полного GGUF: d0c2ac093a77c402f2ddc23a64f68b7c70cfef151899b33e3a6066247104ced3. Если не совпадает — не отключайте проверку и не загружайте неподтверждённый файл.

## Восстановление на другом Windows ПК

~~~powershell
git lfs install
git clone --branch 123456768 https://github.com/Military05/Hackathon.git Contour
cd Contour
git lfs pull
py -3.12 scripts/qwen_bundle.py verify
py -3.12 scripts/qwen_bundle.py restore --destination 'D:\LocalModels\Qwen3-4B-Q4_K_M.gguf'
~~~

Установите Python 3.12, Git LFS, LM Studio/Bionic и необходимые драйверы GPU. Импортируйте восстановленный GGUF в LM Studio/Bionic и настройте identifier hackathon-qwen3-4b, context 8192, parallel 1, API http://127.0.0.1:1234/v1.

Разверните зависимости Python по scripts/setup.ps1 -WithAI, создайте собственный .env на основе .env.example, и создайте новую SQLite штатным процессом начальной установки либо укажите свою ранее сохранённую базу (документы docs/AI_CONNECTION.md и docs/DEVICE_LAUNCH.md). Скрипт scripts/start-ai.ps1 требует абсолютный путь существующей БД, например:

~~~powershell
powershell -ExecutionPolicy Bypass -File scripts/start-ai.ps1 -Database 'C:\ContourData\dispatch.db' -Port 8000
~~~

Проверьте /api/health (MLP ready, агент ready, авторизация включена) и затем выполните настоящий тестовый AI-анализ. Проверка статуса не заменяет проверку ответа модели.

## Границы восстановления
Git не переносит установленные приложения, драйверы, личные пароли, .env и рабочую SQLite с аккаунтами/историей. Не загружайте эти данные в публичный GitHub. Для полного клона пользовательских данных нужна отдельная защищённая передача резервной копии SQLite.
