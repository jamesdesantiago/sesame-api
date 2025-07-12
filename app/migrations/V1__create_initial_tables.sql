-- app/db/migrations/V1__create_initial_tables.sql

-- Users Table
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    firebase_uid VARCHAR(255) UNIQUE,
    email VARCHAR(255) UNIQUE NOT NULL,
    username VARCHAR(50) UNIQUE,
    display_name VARCHAR(100),
    profile_picture VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Privacy Settings
    profile_is_public BOOLEAN NOT NULL DEFAULT TRUE,
    lists_are_public BOOLEAN NOT NULL DEFAULT TRUE,
    allow_analytics BOOLEAN NOT NULL DEFAULT TRUE
);

-- Lists Table
CREATE TABLE IF NOT EXISTS lists (
    id SERIAL PRIMARY KEY,
    owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    is_private BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Places Table
CREATE TABLE IF NOT EXISTS places (
    id SERIAL PRIMARY KEY,
    list_id INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
    place_id VARCHAR(255) NOT NULL, -- External ID (e.g., from Google Places)
    name VARCHAR(200) NOT NULL,
    address VARCHAR(300),
    latitude NUMERIC(10, 7),
    longitude NUMERIC(10, 7),
    rating VARCHAR(50),
    notes TEXT,
    visit_status VARCHAR(50),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(list_id, place_id) -- A place can only be in a list once
);

-- List Collaborators (Many-to-Many)
CREATE TABLE IF NOT EXISTS list_collaborators (
    list_id INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (list_id, user_id)
);

-- User Follows (Many-to-Many)
CREATE TABLE IF NOT EXISTS user_follows (
    follower_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    followed_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (follower_id, followed_id),
    CHECK (follower_id <> followed_id)
);

-- Notifications Table
CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(100) NOT NULL,
    message TEXT NOT NULL,
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    "timestamp" TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Add Indexes for performance
CREATE INDEX IF NOT EXISTS idx_lists_owner_id ON lists(owner_id);
CREATE INDEX IF NOT EXISTS idx_places_list_id ON places(list_id);
CREATE INDEX IF NOT EXISTS idx_collaborators_user_id ON list_collaborators(user_id);
CREATE INDEX IF NOT EXISTS idx_user_follows_followed_id ON user_follows(followed_id);
CREATE INDEX IF NOT EXISTS idx_notifications_user_id ON notifications(user_id);
