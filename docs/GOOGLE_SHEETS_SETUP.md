# Настройка интеграции с Google Sheets

Чтобы экспорт хуков в Google Таблицы работал, нужны **два источника данных** и **одна таблица**.

---

## Краткий чеклист (всё ли готово)

| Шаг | Сделано? |
|-----|----------|
| JSON-ключ (credentials) лежит в проекте, путь указан в `.env` | |
| В `.env` заданы `GOOGLE_SHEET_CREDENTIALS_PATH` и `GOOGLE_SHEET_ID` | |
| Если лист называется не "Hook Analytics", задан `GOOGLE_SHEET_WORKSHEET_NAME` (напр. `Hooks CRM`) | |
| Таблица расшарена на email из JSON (`client_email`) с правом **Редактор** | |
| В Google Cloud включены **Google Sheets API** и **Google Drive API** | |

После каждого анализа скриншота бот сам вызывает экспорт в таблицу (если `GOOGLE_SHEET_ID` задан).

---

## 1. Service Account (ключ доступа к API)

Бот авторизуется в Google через **Service Account** — это «робот-аккаунт» с собственным email и JSON-ключом.

### Шаги

1. **Google Cloud Console**  
   Перейди в [console.cloud.google.com](https://console.cloud.google.com).

2. **Проект**  
   Создай новый проект или выбери существующий.

3. **Включи API**  
   - Меню → **APIs & Services** → **Library**  
   - Найди и включи: **Google Sheets API** и **Google Drive API**.

4. **Service Account**  
   - Меню → **APIs & Services** → **Credentials**  
   - **Create Credentials** → **Service account**  
   - Имя (например: `video-stats-bot`) → **Create and Continue** → **Done**.

5. **JSON-ключ**  
   - В списке Credentials открой созданный Service Account  
   - Вкладка **Keys** → **Add Key** → **Create new key** → **JSON** → **Create**  
   - Скачается файл (например `project-name-xxxxx.json`).

6. **Сохрани файл**  
   Положи JSON в корень проекта, например:  
   `credentials.json`  
   Или в любое место и укажи полный путь в `.env`.

---

## 2. Переменные окружения (.env)

Добавь в `.env`:

```env
# Путь к JSON-ключу (относительно корня проекта или абсолютный)
GOOGLE_SHEET_CREDENTIALS_PATH=./credentials-gsheet.json

# ID таблицы (из URL: часть между /d/ и /edit)
GOOGLE_SHEET_ID=твой_id_таблицы

# Название листа, куда писать (если не "Hook Analytics"). Например для листа "Hooks CRM":
# GOOGLE_SHEET_WORKSHEET_NAME=Hooks CRM
```

**Как узнать GOOGLE_SHEET_ID**  
Открой таблицу в браузере. В URL будет что-то вроде:

```
https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit
```

ID — это часть между `/d/` и `/edit`:  
`1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms`

---

## 3. Таблица и доступ для Service Account

Таблица и лист должны **уже существовать**. Бот только добавляет строки, листы не создаёт.

1. **Создай таблицу** (или используй существующую) в [sheets.google.com](https://sheets.google.com).

2. **Лист**  
   В таблице должен быть лист, куда писать данные. По умолчанию бот ищет лист **`Hook Analytics`**.  
   Если у тебя лист называется иначе (например **Hooks CRM**), задай в `.env`:  
   `GOOGLE_SHEET_WORKSHEET_NAME=Hooks CRM`

3. **Дай доступ Service Account**  
   - В JSON-файле ключа найди поле `"client_email"`.  
     Пример: `video-stats-bot@project-name-xxxxx.iam.gserviceaccount.com`  
   - В Google Таблице: **Поделиться** (Share)  
   - Добавь этот email как **Редактор** (Editor)  
   - Сохрани.

Без этого шага бот получит ошибку доступа к таблице.

---

## 4. Итоговый чеклист

| Что | Где/как |
|-----|--------|
| Google Sheets API | Включен в Google Cloud |
| Google Drive API | Включен в Google Cloud |
| Service Account | Создан в проекте |
| JSON-ключ | Скачан, путь в `GOOGLE_SHEET_CREDENTIALS_PATH` |
| Таблица | Создана, ID в `GOOGLE_SHEET_ID` |
| Лист (по умолчанию `Hook Analytics`, или `GOOGLE_SHEET_WORKSHEET_NAME`, напр. `Hooks CRM`) | Есть в этой таблице |
| Доступ по email | Таблица расшарена на `client_email` из JSON как Редактор |

После этого при вызове `export_hook_to_sheet(video_data)` бот будет добавлять строки в лист **Hook Analytics** с колонками:  
**Date | Platform | Video Title | Hook Score | Retention 3s | Verdict**.
