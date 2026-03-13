-- Users
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    steam_id VARCHAR(255) UNIQUE,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Backlog Items
CREATE TABLE backlog_items (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    steam_appid VARCHAR(255),
    rawg_id VARCHAR(255),
    title VARCHAR(255) NOT NULL,
    cover_url TEXT,
    genres TEXT[],
    estimated_playtime INTEGER,
    hours_played FLOAT DEFAULT 0,
    progress_percent FLOAT DEFAULT 0,
    status VARCHAR(50) DEFAULT 'not_started',
    last_interacted_at TIMESTAMP DEFAULT NOW(),
    added_at TIMESTAMP DEFAULT NOW()
);

-- Taste Profile
CREATE TABLE taste_profile (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    genre VARCHAR(255) NOT NULL,
    weight FLOAT DEFAULT 1.0,
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, genre)
);

-- Recommendation Logs
CREATE TABLE recommendation_logs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    session_length_requested INTEGER,
    items_scored INTEGER,
    top_pick_id INTEGER REFERENCES backlog_items(id),
    score_breakdown JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_backlog_user_status_interacted 
ON backlog_items(user_id, status, last_interacted_at);

CREATE INDEX idx_taste_profile_user 
ON taste_profile(user_id);