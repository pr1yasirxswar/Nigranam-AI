-- Run ONCE in Supabase -> SQL Editor, AFTER the backend has booted and
-- created its tables. Supabase exposes the `public` schema over its REST API
-- (PostgREST) using the public anon key; without RLS anyone holding that key
-- could read/modify `users`, `sessions`, etc. This app never uses PostgREST
-- (it connects directly as the `postgres` role, which bypasses RLS), so
-- enabling RLS with no policies simply locks the REST door.
DO $$
DECLARE t record;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t.tablename);
  END LOOP;
END $$;
