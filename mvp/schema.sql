
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS метаданные (
    ключ TEXT PRIMARY KEY,
    значение TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS кампании (
    кампания_id TEXT PRIMARY KEY,
    название TEXT NOT NULL,
    цель TEXT,
    дата_начала TEXT,
    дата_окончания TEXT,
    плановый_бюджет REAL,
    статус TEXT NOT NULL,
    тип_данных TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS размещения (
    размещение_id TEXT PRIMARY KEY,
    кампания_id TEXT NOT NULL,
    канал TEXT NOT NULL,
    тип_канала TEXT NOT NULL,
    креатив TEXT NOT NULL,
    предложение TEXT,
    время_публикации TEXT,
    стоимость REAL,
    валюта TEXT NOT NULL DEFAULT 'RUB',
    токен TEXT NOT NULL UNIQUE,
    статус TEXT NOT NULL,
    тип_данных TEXT NOT NULL,
    FOREIGN KEY(кампания_id) REFERENCES кампании(кампания_id)
);

CREATE TABLE IF NOT EXISTS пользователи (
    пользователь_id TEXT PRIMARY KEY,
    создан TEXT NOT NULL,
    способ_склейки TEXT NOT NULL,
    тип_данных TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS переходы (
    переход_id TEXT PRIMARY KEY,
    размещение_id TEXT NOT NULL,
    пользователь_id TEXT,
    время TEXT NOT NULL,
    источник TEXT NOT NULL,
    тип_данных TEXT NOT NULL,
    FOREIGN KEY(размещение_id) REFERENCES размещения(размещение_id),
    FOREIGN KEY(пользователь_id) REFERENCES пользователи(пользователь_id)
);

CREATE TABLE IF NOT EXISTS события (
    событие_id TEXT PRIMARY KEY,
    пользователь_id TEXT,
    переход_id TEXT,
    размещение_id TEXT,
    тип_события TEXT NOT NULL,
    время TEXT NOT NULL,
    свойства TEXT,
    тип_данных TEXT NOT NULL,
    FOREIGN KEY(пользователь_id) REFERENCES пользователи(пользователь_id),
    FOREIGN KEY(переход_id) REFERENCES переходы(переход_id),
    FOREIGN KEY(размещение_id) REFERENCES размещения(размещение_id)
);

CREATE TABLE IF NOT EXISTS лиды (
    лид_id TEXT PRIMARY KEY,
    пользователь_id TEXT NOT NULL,
    исходный_переход_id TEXT,
    интерес TEXT,
    время TEXT NOT NULL,
    статус TEXT NOT NULL,
    тип_данных TEXT NOT NULL,
    FOREIGN KEY(пользователь_id) REFERENCES пользователи(пользователь_id),
    FOREIGN KEY(исходный_переход_id) REFERENCES переходы(переход_id)
);

CREATE TABLE IF NOT EXISTS заказы (
    заказ_id TEXT PRIMARY KEY,
    пользователь_id TEXT NOT NULL,
    лид_id TEXT,
    время TEXT NOT NULL,
    сумма REAL NOT NULL,
    статус TEXT NOT NULL,
    номер_наблюдаемой_покупки INTEGER,
    исторический_ключ TEXT,
    тип_данных TEXT NOT NULL,
    FOREIGN KEY(пользователь_id) REFERENCES пользователи(пользователь_id),
    FOREIGN KEY(лид_id) REFERENCES лиды(лид_id)
);

CREATE TABLE IF NOT EXISTS оплаты (
    оплата_id TEXT PRIMARY KEY,
    заказ_id TEXT NOT NULL UNIQUE,
    пользователь_id TEXT NOT NULL,
    время TEXT NOT NULL,
    сумма REAL NOT NULL,
    статус TEXT NOT NULL,
    тип_данных TEXT NOT NULL,
    FOREIGN KEY(заказ_id) REFERENCES заказы(заказ_id),
    FOREIGN KEY(пользователь_id) REFERENCES пользователи(пользователь_id)
);

CREATE TABLE IF NOT EXISTS атрибуция (
    заказ_id TEXT NOT NULL,
    размещение_id TEXT,
    категория TEXT NOT NULL,
    порядок_касания INTEGER NOT NULL,
    всего_касаний INTEGER NOT NULL,
    вес REAL NOT NULL,
    атрибутированная_выручка REAL NOT NULL,
    модель TEXT NOT NULL,
    PRIMARY KEY(заказ_id, категория, порядок_касания, размещение_id),
    FOREIGN KEY(заказ_id) REFERENCES заказы(заказ_id),
    FOREIGN KEY(размещение_id) REFERENCES размещения(размещение_id)
);

CREATE TABLE IF NOT EXISTS причинный_пример (
    показатель TEXT PRIMARY KEY,
    значение REAL,
    единица TEXT,
    пояснение TEXT,
    тип_данных TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS прогноз (
    дата TEXT PRIMARY KEY,
    заказы REAL NOT NULL,
    выручка REAL NOT NULL,
    тип_данных TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS исторические_события_маркетинга (
    событие_id TEXT PRIMARY KEY,
    дата_начала TEXT,
    дата_окончания TEXT,
    канал TEXT,
    вид TEXT,
    описание TEXT,
    качество_источника TEXT,
    тип_данных TEXT NOT NULL
);

-- Дополнительные поля не меняют формат существующих таблиц и импортов.
CREATE TABLE IF NOT EXISTS placement_details (
    placement_id TEXT PRIMARY KEY REFERENCES размещения(размещение_id),
    name TEXT NOT NULL,
    publication_url TEXT NOT NULL DEFAULT '',
    comment TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS manager_dialogues (
    dialogue_id TEXT PRIMARY KEY,
    lead_id TEXT NOT NULL UNIQUE REFERENCES лиды(лид_id),
    user_key TEXT NOT NULL REFERENCES пользователи(пользователь_id),
    product TEXT NOT NULL,
    started_at TEXT NOT NULL,
    manager TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS event_links (
    event_id TEXT PRIMARY KEY REFERENCES события(событие_id),
    lead_id TEXT REFERENCES лиды(лид_id),
    order_id TEXT REFERENCES заказы(заказ_id),
    payment_id TEXT REFERENCES оплаты(оплата_id)
);
CREATE INDEX IF NOT EXISTS clicks_user_time ON переходы(пользователь_id, время);
CREATE INDEX IF NOT EXISTS events_user_time ON события(пользователь_id, время);
CREATE INDEX IF NOT EXISTS orders_lead ON заказы(лид_id);
CREATE INDEX IF NOT EXISTS payments_user_time ON оплаты(пользователь_id, время);
