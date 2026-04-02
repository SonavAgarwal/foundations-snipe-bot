# Foundations Bot

Foundations Bot is a Discord bot for tracking family points from spotting posts and manual event adjustments. It listens for image posts in one configured spotting channel, stores each scored snipe as its own database row, and aggregates leaderboards and graphs from those rows.

## What It Tracks

Spotting:
- Post an image in the configured spotting channel.
- Tag the people in the photo.
- Each tagged eligible person is worth 1 point for the sender's family.
- A sender can only score the same tagged person once per day.
- The point is attributed to the sender for the people leaderboard.

HOOPing:
- If the sender tags at least two other people from the sender's own family, and there are at least three eligible team members present total, the sender's family gets a 2 point HOOPing bonus.
- HOOPing points are not attributed to any person.
- A single post can score both spotting points and a HOOPing bonus.

Manual adjustments:
- Use `/adjust` for POOPing, TikToks, outfit coordination, FC events, or any other admin-scored event.
- Adjustments count as events and affect family totals, but not personal totals.

Voids:
- Use `/void event_id:<id>` to void any active scoring row you want.
- Or use `/void sender:<member> sniped:<member>` to void the most recent active spotting row for that sender-target pair.
- Use `/recent-events` to list recent rows and their IDs.
- Voiding removes exactly that row. If you void a spotting row, it does not remove other tagged people from the same message, and it does not remove any HOOPing bonus from that message.

## Commands

Admin commands:
- `/set-lship-role <role>`
- `/set-genmem-role <role>`
- `/setfam <role/name/NONE> <list of usernames or tags>`
- `/setchannel <sniping channel>`
- `/adjust <famname> <points> <reason>`
- `/void [event_id] [sender] [sniped]`
- `/recent-events [limit]`

Public commands:
- `/leaderboard [full]`
- `/graph`

Notes about `/setfam`:
- `family` can be a plain name, a role mention, or `NONE`.
- The `members` argument is a freeform string. Mentions are the most reliable format.
- Plain usernames and display names are also supported when they match exactly.

## Behavior Rules

- The bot only processes messages in the configured spotting channel.
- The bot only counts messages that include an image attachment.
- If leadership and general member roles are configured, only members with one of those roles count as eligible spotted people.
- The sender must also be an eligible member when those roles are configured.
- Family points are always awarded to the sender's family.
- Individual points only come from spotting rows, never from HOOPing or manual adjustments.

## Local Development

1. Copy `.env.example` to `.env` and fill in the Discord token.
2. Start MySQL:

```bash
docker compose up -d mysql
```

3. Start the bot locally:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Or run the full stack in Docker:

```bash
docker compose up --build
```

## Environment Variables

Required:
- `DISCORD_TOKEN`
- `DB_USER`
- `DB_PASSWORD`
- `DB_NAME`

Common optional values:
- `DISCORD_GUILD_ID` for faster guild-scoped slash command sync during development
- `BOT_TIMEZONE`, default `America/Los_Angeles`
- `BOT_NAME`, default `Foundations Bot`
- `DB_HOST`, default `127.0.0.1`
- `DB_PORT`, default `3306`
- `PORT`, default `8080`
- `INSTANCE_CONNECTION_NAME` for Cloud Run with Cloud SQL
- `DATABASE_URL` if you want to bypass the individual DB env vars entirely

## Cloud Run

This repo includes a Dockerfile and a deployment script at [scripts/deploy_cloud_run.sh](/Users/sonavagarwal/Documents/GitHub/foundations-snipe-bot/scripts/deploy_cloud_run.sh). The script:

- enables the required APIs
- creates an Artifact Registry Docker repository if needed
- builds and pushes the image with Cloud Build
- deploys the image to Cloud Run
- optionally attaches Cloud SQL if `INSTANCE_CONNECTION_NAME` is set

Usage:

1. Copy [deploy/cloudrun.env.yaml.example](/Users/sonavagarwal/Documents/GitHub/foundations-snipe-bot/deploy/cloudrun.env.yaml.example) to `deploy/cloudrun.env.yaml` and fill it in.
2. Export the deployment variables:

```bash
export PROJECT_ID="your-project-id"
export REGION="us-west1"
export SERVICE_NAME="foundations-bot"
export REPOSITORY="foundations-bot"
export INSTANCE_CONNECTION_NAME="your-project:your-region:your-instance"
```

3. Run:

```bash
bash scripts/deploy_cloud_run.sh
```

Cloud Run note:
- Because Discord bots keep a long-lived gateway connection open, the deployment script pins Cloud Run to `min-instances=1` and `max-instances=1`.
- Local MySQL is fine for local development, but Cloud Run needs a reachable MySQL instance. The intended GCP path is Cloud SQL for MySQL.

## Implementation Notes

- Storage uses SQLAlchemy 2.0 with typed models instead of the old SQLite-specific backend.
- Every scored spotting target is stored as an individual event row and aggregated later for reports.
- The app exposes a lightweight HTTP health endpoint on `/healthz` so Cloud Run has something to probe.

Built with Codex 5.4.
