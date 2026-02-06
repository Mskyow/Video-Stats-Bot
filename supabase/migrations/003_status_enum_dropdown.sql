-- 003: Делаем status типом ENUM — в Supabase Table Editor появится выпадающий список
-- Выполнить в SQL Editor в дашборде Supabase (один раз)

-- 1. Убираем старый CHECK (он больше не нужен — enum сам ограничивает значения)
ALTER TABLE users DROP CONSTRAINT IF EXISTS chk_users_status;

-- 2. Создаём тип перечисления
DO $$ BEGIN
    CREATE TYPE user_status_enum AS ENUM ('pending', 'approved', 'rejected');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- 3. Меняем колонку status на enum
ALTER TABLE users
  ALTER COLUMN status TYPE user_status_enum
  USING status::user_status_enum;

-- 4. Дефолт для новых строк
ALTER TABLE users
  ALTER COLUMN status SET DEFAULT 'pending'::user_status_enum;

-- Готово. В Table Editor при клике на ячейку status появится выпадающий список:
-- pending | approved | rejected
