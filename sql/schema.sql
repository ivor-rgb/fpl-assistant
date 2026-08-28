-- Run this once in your Supabase project's SQL editor before first use.

create table if not exists fpl_run_log (
    id bigint generated always as identity primary key,
    team_id bigint not null,
    gameweek int not null,
    run_at timestamptz not null default now()
);

create table if not exists recommendations (
    id bigint generated always as identity primary key,
    team_id bigint not null,
    gameweek int not null,
    transfers_out jsonb,
    transfers_in jsonb,
    hit_taken int,
    expected_points_gain numeric,
    starting_xi jsonb,
    chip_evaluations jsonb,
    created_at timestamptz not null default now()
);

create table if not exists predictions (
    id bigint generated always as identity primary key,
    gameweek int not null,
    player_id int not null,
    predicted_points numeric,
    actual_points numeric,
    created_at timestamptz not null default now(),
    unique (gameweek, player_id)
);

create table if not exists league_snapshots (
    id bigint generated always as identity primary key,
    league_id bigint not null,
    league_name text,
    gameweek int not null,
    manager_name text,
    rank int,
    total_points int,
    created_at timestamptz not null default now()
);
