# Foundations Bot

Foundations Bot is a Discord bot for tracking family points from spotting posts and manual adjustments. It stores event rows in an on-disk SQLite database and is meant to run simply on one VM.

Family membership is role-based. Discord family roles are the source of truth.

## What It Tracks

Spotting:
- Post an image in the configured spotting channel.
- Tag the people in the photo.
- Each tagged eligible person is worth 1 point for the sender's family.
- A sender can only score the same tagged person once per day.
- The point is attributed to the sender for the people leaderboard.
- The bot reacts on the spotting message with the current total active points for that message.
- If a message has zero active points, the bot reacts with `❌`.
- If the sender is eligible but does not have a tracked family role, the bot reacts with `🔥`, `📸`, or `🤨` and records no points.
- The bot does not post a reply when points are counted. Reactions are the scoring feedback.

HOOPing:
- If the sender tags at least two other people from the sender's own family, and there are at least three eligible team members present total, the sender's family gets a 2 point HOOPing bonus.
- HOOPing points are not attributed to any person.
- A single post can score both spotting points and a HOOPing bonus.

Manual adjustments:
- Use `/adjust` for POOPing, TikToks, outfit coordination, FC events, or any other admin-scored event.
- Adjustments count as events and affect family totals, but not personal totals.
- `/adjust` can target a family directly or use `event_id` to apply the adjustment to an existing event's family.

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
- `/adjust <points> <reason> [family] [event_id]`
- `/void [event_id] [sender] [sniped]`
- `/recent-events [limit]`

Public commands:
- `/hello`
- `/leaderboard [full]`
- `/graph`

Notes about `/setfam`:
- `family` can be an exact role name, a role mention, or `NONE`.
- The `members` argument is a freeform string. Mentions are the most reliable format.
- Plain usernames and display names are also supported when they match exactly.
- `/setfam` updates actual Discord roles. The bot needs `Manage Roles`, and the bot role must be above the family roles in the server hierarchy.

## Environment

Required:
- `DISCORD_TOKEN`

Common optional values:
- `DISCORD_GUILD_ID` for guild-scoped slash command sync and hard single-server enforcement
- `BOT_TIMEZONE`, default `America/Los_Angeles`
- `BOT_NAME`, default `Foundations Bot`
- `BOT_ADMIN_ROLE` to restrict mutating slash commands to members with that exact role name
- `SQLITE_PATH`, default `data/foundations_bot.db`
- `DATABASE_URL` only if you want to override SQLite manually

Warning:
- If `BOT_ADMIN_ROLE` is left blank, mutating commands fall back to Discord `Manage Server` or `Manage Roles` permissions instead of one specific admin role.

## Local Run

1. Copy `.env.example` to `.env`.
2. Fill in `DISCORD_TOKEN`.
3. Run:

```bash
docker compose up --build
```

That mounts `./data` into the container and stores SQLite at `./data/foundations_bot.db`.

If you want to run without Docker:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## VM Deploy

This bot is meant to run simply on one VM with one Docker container and one SQLite file on disk.

Recommended shape:
- one Ubuntu VM
- Docker installed
- repo cloned onto the VM
- SQLite stored on the VM disk in `./data/foundations_bot.db`

Create a cheap VM on GCP:

```bash
gcloud compute instances create foundations-bot-vm \
  --zone=us-central1-a \
  --machine-type=e2-micro \
  --boot-disk-size=20GB \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud
```

SSH into it:

```bash
gcloud compute ssh foundations-bot-vm --zone=us-central1-a
```

Install git on the VM:

```bash
sudo apt-get update
sudo apt-get install -y git
```

First-time Docker install on Ubuntu:

```bash
bash scripts/install_docker_ubuntu.sh
```

After that, from the repo root on the VM:

```bash
cp .env.example .env
# fill in DISCORD_TOKEN, DISCORD_GUILD_ID, BOT_ADMIN_ROLE if wanted
bash scripts/deploy_vm.sh
```

What the deploy script does:
- builds the Docker image locally on the VM
- recreates the `foundations-bot` container
- mounts `./data` into `/data`
- stores SQLite at `/data/foundations_bot.db`
- sets restart policy to `unless-stopped`

Useful VM commands:

```bash
docker logs -f foundations-bot
docker restart foundations-bot
docker exec -it foundations-bot /bin/bash
bash scripts/reset_sqlite.sh
```

## Implementation Notes

- Storage uses SQLAlchemy 2.0 with typed models, but the default database is SQLite on disk.
- Every scored spotting target is stored as an individual event row and aggregated later for reports.
- The app exposes a lightweight HTTP health endpoint on `/healthz`.
- If `DISCORD_GUILD_ID` is set, the bot ignores other guilds, rejects commands there, auto-leaves new guilds, and leaves any extra guilds on startup.

Built with Codex 5.4.
