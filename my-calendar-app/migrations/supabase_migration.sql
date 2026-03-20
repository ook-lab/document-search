-- Run this in Supabase SQL Editor
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS checklists JSONB DEFAULT '[]'::jsonb;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS members_data JSONB DEFAULT '[]'::jsonb;
