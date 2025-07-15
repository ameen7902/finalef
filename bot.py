import json
import time
import random
import os
from collections import defaultdict
import base64 # For decoding Firebase key

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters,
    ContextTypes, ConversationHandler
)
from flask import Flask, request
from threading import Thread
import asyncio

# Firebase Imports
import firebase_admin
from firebase_admin import credentials, db

# --- Flask App for Webhooks (Required for Railway.app deployment) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_flask_app():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def keep_alive_flask():
    t = Thread(target=run_flask_app)
    t.start()
# --------------------------------------------------------------------

# === CONFIG ===
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROUP_ID = int(os.environ.get("GROUP_ID", -1002835703789))
ADMIN_ID = int(os.environ.get("ADMIN_ID", 7366894756))

# Firebase Configuration
# This environment variable will hold the base64 encoded Firebase service account key
FIREBASE_SERVICE_ACCOUNT_B64 = os.environ.get("FIREBASE_SERVICE_ACCOUNT_B64")
FIREBASE_DATABASE_URL = os.environ.get("FIREBASE_DATABASE_URL") # e.g., "https://your-project-id-default-rtdb.firebaseio.com/"

# Global Firebase DB reference
firebase_db_ref = None

# FIFA World Cup style teams (32 teams)
TEAM_LIST = [
    ("🇧🇷", "Brazil"), ("🇦🇷", "Argentina"), ("🇫🇷", "France"), ("🇩🇪", "Germany"),
    ("🇪🇸", "Spain"), ("🇮🇹", "Italy"), ("🏴󠁧󠁢󠁥󠁮󠁧󠁿", "England"), ("🇵🇹", "Portugal"),
    ("🇳🇱", "Netherlands"), ("🇺🇾", "Uruguay"), ("🇧🇪", "Belgium"), ("🇭🇷", "Croatia"),
    ("🇨🇭", "Switzerland"), ("🇲🇽", "Mexico"), ("🇯🇵", "Japan"), ("🇺🇸", "USA"),
    ("🇨🇴", "Colombia"), ("🇸🇳", "Senegal"), ("🇵🇱", "Poland"), ("🇰🇷", "South Korea"),
    ("🇨🇱", "Chile"), ("🇷🇸", "Serbia"), ("🇦🇺", "Australia"), ("🇩🇰", "Denmark"),
    ("🇲🇦", "Morocco"), ("🇬🇭", "Ghana"), ("🇨🇲", "Cameroon"), ("🇪🇨", "Ecuador"),
    ("🇨🇦", "Canada"), ("🇶🇦", "Qatar"), ("🇸🇦", "Saudi Arabia"), ("🇮🇷", "Iran")
]

# Conversation state for PES name entry
REGISTER_PES = 1 

# === FIREBASE UTILITIES ===
def init_firebase():
    global firebase_db_ref
    if firebase_db_ref:
        return # Already initialized

    if not FIREBASE_SERVICE_ACCOUNT_B64 or not FIREBASE_DATABASE_URL:
        print("Error: Firebase environment variables not set. Cannot initialize Firebase.")
        return

    try:
        # Decode the base64 string back to JSON bytes
        service_account_info_bytes = base64.b64decode(FIREBASE_SERVICE_ACCOUNT_B64)
        # Load the JSON bytes into a Python dict
        service_account_info = json.loads(service_account_info_bytes)

        # Initialize Firebase app
        cred = credentials.Certificate(service_account_info)
        firebase_admin.initialize_app(cred, {
            'databaseURL': FIREBASE_DATABASE_URL
        })
        firebase_db_ref = db.reference('/') # Get a reference to the root of your database
        print("Successfully initialized Firebase!")
    except Exception as e:
        print(f"Error initializing Firebase: {e}")
        firebase_db_ref = None # Indicate failure

def load_state(key, default_value=None):
    if not firebase_db_ref:
        print(f"Firebase not initialized for key {key}. Returning default.")
        return default_value if default_value is not None else {}
    try:
        # Get data from a specific path in Firebase Realtime Database
        data = firebase_db_ref.child(key).get()
        if data is None: # Firebase returns None if path doesn't exist
            return default_value if default_value is not None else {}
        return data
    except Exception as e:
        print(f"Error loading data from Firebase for key {key}: {e}")
        return default_value if default_value is not None else {}

def save_state(key, data):
    if not firebase_db_ref:
        print(f"Firebase not initialized for key {key}. Cannot save data.")
        return
    try:
        # Set data at a specific path in Firebase Realtime Database
        firebase_db_ref.child(key).set(data)
    except Exception as e:
        print(f"Error saving data to Firebase for key {key}: {e}")

# === LOCKING SYSTEM (now in Firebase) ===
def is_locked():
    lock = load_state("lock") # Loads from Firebase
    if not lock:
        return False
    # Check for timeout (5 minutes)
    if time.time() - lock.get("start_time", 0) > 300:
        unlock_user()
        return False
    return True

def lock_user(user_id):
    save_state("lock", {"user_id": user_id, "start_time": time.time(), "selected_team": None})

def unlock_user():
    save_state("lock", {})

def set_selected_team(team):
    lock = load_state("lock")
    if lock:
        lock["selected_team"] = team
        save_state("lock", lock)

def get_locked_user():
    return load_state("lock").get("user_id")

def get_locked_team():
    return load_state("lock").get("selected_team")

# === BOT COMMANDS ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Welcome to the eFootball World Cup Tournament!\nUse /register to join.")

async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rules_list = load_state("rules_list", default_value=[]) # Load rules from Firebase
    if not rules_list:
        await update.message.reply_text("ℹ️ No rules added yet. Admin can use /addrule.")
        return

    formatted = "\n".join([f"{i+1}. {line.strip()}" for i, line in enumerate(rules_list)])
    await update.message.reply_text(f"📜 Tournament Rules:\n\n{formatted}")

async def addrule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Only the admin can use this command.")
        return

    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("⚠️ Usage: /addrule Your rule text here")
        return

    rules_list = load_state("rules_list", default_value=[])
    rules_list.append(text)
    save_state("rules_list", rules_list)
    await update.message.reply_text("✅ Rule added.")

async def players_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    players = load_state("players")

    if not players:
        await update.message.reply_text("❌ No players have registered yet.")
        return

    reply = "👥 Registered Players:\n\n"
    for p in players.values():
        reply += f"{p['team']} — @{p['username']} (🎮 {p['pes']})\n"

    await update.message.reply_text(reply)

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if update.effective_chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("❌ Please use /register in the tournament group.")
        return

    if is_locked():
        await update.message.reply_text("⚠️ Another player is registering. Please try again in a few minutes.")
        return

    players = load_state("players")
    if str(user.id) in players:
        await update.message.reply_text("✅ You are already registered.")
        return

    tournament_state = load_state("tournament_state")
    current_stage = tournament_state.get("stage", "registration")
    if current_stage != "registration":
        await update.message.reply_text("❌ Registration is closed. The tournament has already started.")
        return

    lock_user(user.id)

    try:
        await context.bot.send_message(
            chat_id=user.id,
            text="📝 Let's get you registered!\nPlease select your national team:",
            reply_markup=InlineKeyboardMarkup(build_team_buttons())
        )
        await update.message.reply_text("📩 Check your DM to complete registration.")
    except Exception as e:
        print(f"Error sending DM for registration: {e}")
        await update.message.reply_text("❌ Couldn't send DM. Please start the bot first: @e_tournament_bot")
        unlock_user()

def build_team_buttons():
    players = load_state("players")
    taken_teams = {p['team'] for p in players.values()}
    available = [(flag, name) for flag, name in TEAM_LIST if f"{flag} {name}" not in taken_teams]

    keyboard = []
    row = []
    for flag, name in available:
        row.append(InlineKeyboardButton(f"{flag} {name}", callback_data=f"team_select:{flag} {name}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row: # Add any remaining buttons
        keyboard.append(row)
    return keyboard

async def handle_team_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    if user.id != get_locked_user():
        await query.edit_message_text("⚠️ You are not allowed to register now. Please wait your turn.")
        return ConversationHandler.END

    team_full_name = query.data.split(':', 1)[1] # Extract team name from callback_data
    set_selected_team(team_full_name)

    await query.edit_message_text(f"✅ Team selected: {team_full_name}\n\nNow send your PES username:")
    return REGISTER_PES

async def receive_pes_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    pes_name = update.message.text.strip()
    players = load_state("players")

    team = get_locked_team()
    if not team:
        await update.message.reply_text("❌ Something went wrong. Try /register again.")
        unlock_user()
        return ConversationHandler.END

    players[str(user.id)] = {
        "name": user.first_name,
        "username": user.username or "NoUsername",
        "team": team,
        "pes": pes_name,
        "group": None, # Will be assigned later
        "stats": {"wins": 0, "draws": 0, "losses": 0, "gf": 0, "ga": 0, "points": 0, "gd": 0}
    }

    save_state("players", players)
    unlock_user()

    await context.bot.send_message(chat_id=user.id, text=f"✅ Registered!\n🏳️ Team: {team}\n🎮 PES: {pes_name}")
    await context.bot.send_message(chat_id=GROUP_ID, text=f"✅ @{user.username or user.first_name} registered as {team}")

    return ConversationHandler.END

async def cancel_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id == get_locked_user():
        unlock_user()
        await update.message.reply_text("❌ Registration cancelled.")
    else:
        await update.message.reply_text("ℹ️ No active registration to cancel.")
    return ConversationHandler.END

async def set_bot_commands(application):
    commands = [
        BotCommand("start", "Start the bot"),
        BotCommand("register", "Register for the tournament"),
        BotCommand("fixtures", "View upcoming matches"),
        BotCommand("standings", "View group standings"),
        BotCommand("rules", "Show tournament rules"),
        BotCommand("players", "List registered players"),
        # Admin Commands
        BotCommand("addrule", "Admin: Add a rule"),
        BotCommand("start_tournament", "Admin: Start the group stage"),
        BotCommand("addscore", "Admin: Add match scores"),
        BotCommand("reset_tournament", "Admin: Clear all tournament data"),
    ]
    await application.bot.set_my_commands(commands)
    print("Bot commands set.")

# === TOURNAMENT LOGIC ===

async def start_tournament(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Only the admin can start the tournament.")
        return

    players = load_state("players")
    if len(players) != 32:
        await update.message.reply_text(f"❌ Need exactly 32 players to start the tournament. Currently have {len(players)}.")
        return

    tournament_state = load_state("tournament_state")
    if tournament_state.get("stage") != "registration":
        await update.message.reply_text("❌ The tournament has already started or is in an advanced stage. Use /reset_tournament to restart.")
        return

    await update.message.reply_text("🎉 The tournament is starting! Generating groups and fixtures...")

    await make_groups(context)
    tournament_state["stage"] = "group_stage"
    save_state("tournament_state", tournament_state)

    await context.bot.send_message(GROUP_ID, "🏆 Group Stage has begun! Check /fixtures for your matches and /standings for current rankings.")


async def make_groups(context: ContextTypes.DEFAULT_TYPE):
    players = load_state("players")
    player_ids = list(players.keys())
    random.shuffle(player_ids)

    groups = defaultdict(list)
    group_names = [f"Group {chr(65 + i)}" for i in range(8)] # Group A, B, C...

    # Assign players to groups
    for i, player_id in enumerate(player_ids):
        group_name = group_names[i % 8]
        groups[group_name].append(player_id)
        players[player_id]['group'] = group_name

    save_state("players", players)
    save_state("groups", {name: ids for name, ids in groups.items()})

    await make_group_fixtures(context, groups)

async def make_group_fixtures(context: ContextTypes.DEFAULT_TYPE, groups: dict):
    fixtures_data = load_state("fixtures")
    group_stage_fixtures = {}

    for group_name, player_ids_in_group in groups.items():
        group_fixtures = []
        # Round-robin: each player plays every other player once
        # 4 teams in a group, so 3 matches per team = 6 matches per group
        # (4 * 3) / 2 = 6
        for i in range(len(player_ids_in_group)):
            for j in range(i + 1, len(player_ids_in_group)):
                player1_id = player_ids_in_group[i]
                player2_id = player_ids_in_group[j]
                group_fixtures.append([player1_id, player2_id, None, None]) # [p1_id, p2_id, score1, score2]
        group_stage_fixtures[group_name] = group_fixtures

    fixtures_data["group_stage"] = group_stage_fixtures
    save_state("fixtures", fixtures_data)

    await context.bot.send_message(GROUP_ID, "🔢 Group fixtures generated! Use /fixtures to see your match schedule.")

async def fixtures(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    players = load_state("players")
    fixtures_data = load_state("fixtures")
    tournament_state = load_state("tournament_state")
    current_stage = tournament_state.get("stage", "registration")

    if user_id not in players:
        await update.message.reply_text("❌ You are not registered for the tournament. Use /register.")
        return

    player_info = players[user_id]
    reply_text = ""
    found_fixture = False

    if current_stage == "group_stage":
        player_group = player_info.get("group")
        if not player_group or player_group not in fixtures_data.get("group_stage", {}):
            await update.message.reply_text("❌ Your group fixtures are not yet available.")
            return

        reply_text += f"📅 Your Group Matches - {player_info['team']} ({player_info['group'] or 'No Group'})\n\n"
        group_matches = fixtures_data["group_stage"][player_group]
        for match in group_matches:
            if user_id in match[0:2]:
                opponent_id = match[1] if match[0] == user_id else match[0]
                opponent_info = players.get(opponent_id)
                if opponent_info:
                    score_status = f"({match[2]}-{match[3]})" if match[2] is not None and match[3] is not None else "(Pending)"
                    reply_text += f"{player_info['team']} vs {opponent_info['team']} {score_status}\n🎮 Opponent: @{opponent_info['username']}\n"
                    found_fixture = True

    elif current_stage in ["round_of_16", "quarter_finals", "semi_finals", "final"]:
        knockout_matches = fixtures_data.get(current_stage, [])
        if not knockout_matches: # If no fixtures for this stage yet
            await update.message.reply_text("❌ Knockout matches for this stage are not yet drawn.")
            return

        reply_text += f"📅 Your Knockout Match - {player_info['team']}\n\n"
        for match in knockout_matches:
            # Check if user is player1 or player2 in the match
            if user_id == match[0] or user_id == match[1]:
                opponent_id = match[1] if match[0] == user_id else match[0]
                opponent_info = players.get(opponent_id)
                if opponent_info:
                    score_status = f"({match[2]}-{match[3]})" if match[2] is not None and match[3] is not None else "(Pending)"
                    reply_text += (
                        f"*{current_stage.replace('_', ' ').title()}:*\n"
                        f"{player_info['team']} vs {opponent_info['team']} {score_status}\n"
                        f"🎮 Opponent: @{opponent_info['username']}\n"
                    )
                    found_fixture = True
                break # A player only has one match in knockout round

    if found_fixture:
        await update.message.reply_text(reply_text, parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ No upcoming match found for you or your matches are already completed for this stage.")

async def group_standings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    players = load_state("players")
    groups_data = load_state("groups")

    if not groups_data:
        await update.message.reply_text("❌ Groups have not been formed yet.")
        return

    all_standings = ""
    for group_name in sorted(groups_data.keys()): # Ensure consistent order of groups
        player_ids = groups_data[group_name]
        standings = []
        for p_id in player_ids:
            player_info = players.get(p_id)
            if player_info:
                stats = player_info.get("stats", {})
                standings.append({
                    "team": player_info['team'],
                    "points": stats.get("points", 0),
                    "gd": stats.get("gd", 0),
                    "gf": stats.get("gf", 0),
                    "wins": stats.get("wins", 0),
                    "draws": stats.get("draws", 0),
                    "losses": stats.get("losses", 0)
                })

        # Sort by points, then goal difference, then goals for (descending)
        standings.sort(key=lambda x: (x['points'], x['gd'], x['gf']), reverse=True)

        group_text = f"📊 *{group_name} Standings:*\n"
        group_text += "Team       | Pts | GD | GF | W-D-L\n"
        group_text += "--------------------------------------\n"
        for team_stat in standings:
            # Pad team name for alignment
            team_display = (team_stat['team'] + "         ")[:10] # Pad and truncate
            group_text += (
                f"{team_display} | {team_stat['points']:<3} | {team_stat['gd']:<2} | {team_stat['gf']:<2} | "
                f"{team_stat['wins']}-{team_stat['draws']}-{team_stat['losses']}\n"
            )
        group_text += "\n"
        all_standings += group_text

    if all_standings:
        await update.message.reply_text(all_standings, parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ No standings available yet.")


# Helper to get the current round's matches for admin score entry
current_admin_matches = {} # Stores matches for /addscore options

async def addscore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized.")
        return

    fixtures_data = load_state("fixtures")
    players_data = load_state("players")
    tournament_state = load_state("tournament_state")
    current_stage = tournament_state.get("stage")

    if not fixtures_data or not current_stage:
        await update.message.reply_text("❌ No matches currently scheduled.")
        return

    reply = f"📋 Matches for {current_stage.replace('_', ' ').title()}:\n\n"
    current_admin_matches.clear() # Clear previous options
    idx = 1

    if current_stage == "group_stage":
        for group_name, matches in fixtures_data.get("group_stage", {}).items():
            for match in matches:
                p1_id, p2_id, score1, score2 = match
                if score1 is None: # Only list pending matches
                    p1 = players_data.get(p1_id)
                    p2 = players_data.get(p2_id)
                    if p1 and p2:
                        current_admin_matches[f"match{idx}"] = {"type": "group", "group": group_name, "p1_id": p1_id, "p2_id": p2_id}
                        reply += f"/match{idx} → {p1['team']} vs {p2['team']} ({group_name})\n"
                        idx += 1
    elif current_stage in ["round_of_16", "quarter_finals", "semi_finals", "final"]:
        for match in fixtures_data.get(current_stage, []):
            p1_id, p2_id, score1, score2 = match
            if score1 is None: # Only list pending matches
                p1 = players_data.get(p1_id)
                p2 = players_data.get(p2_id)
                if p1 and p2:
                    current_admin_matches[f"match{idx}"] = {"type": "knockout", "stage": current_stage, "p1_id": p1_id, "p2_id": p2_id}
                    reply += f"/match{idx} → {p1['team']} vs {p2['team']} ({current_stage.replace('_', ' ').title()})\n"
                    idx += 1

    if not current_admin_matches:
        reply = "✅ All matches for this stage are completed. Use /start_tournament (if admin) to advance or /addscore later for next stage."

    reply += "\nTo add score: /match1 2-1"
    await update.message.reply_text(reply)

async def handle_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return # Not authorized

    text = update.message.text.lower().strip()
    try:
        cmd_parts = text.split(" ", 1)
        if len(cmd_parts) < 2:
            raise ValueError("Score not provided")

        cmd, score_str = cmd_parts
        match_key = cmd[1:] # e.g., "match1"
        goals = score_str.split("-")
        if len(goals) != 2:
            raise ValueError("Invalid score format")

        score1 = int(goals[0])
        score2 = int(goals[1])

        match_info = current_admin_matches.get(match_key)
        if not match_info:
            await update.message.reply_text("❌ Match not found or already processed. Use /addscore to see current matches.")
            return

        match_type = match_info["type"]
        p1_id = match_info["p1_id"]
        p2_id = match_info["p1_id"] # Fix: This was p1_id, should be p2_id. Corrected below.
        p2_id = match_info["p2_id"] # Corrected line

        if match_type == "group":
            await handle_group_score(update, context, match_info["group"], p1_id, p2_id, score1, score2)
        elif match_type == "knockout":
            await handle_knockout_score(update, context, match_info["stage"], p1_id, p2_id, score1, score2)

        # Clear this match from current_admin_matches to prevent re-submission
        if match_key in current_admin_matches:
            del current_admin_matches[match_key]

    except ValueError as ve:
        await update.message.reply_text(f"❌ Invalid format. Use like: /match2 1-0. Error: {ve}")
    except Exception as e:
        print(f"Error in handle_score: {e}")
        await update.message.reply_text("❌ An unexpected error occurred.")


async def handle_group_score(update: Update, context: ContextTypes.DEFAULT_TYPE, group_name: str, p1_id: str, p2_id: str, score1: int, score2: int):
    fixtures_data = load_state("fixtures")
    players = load_state("players")

    # Find and update the score in fixtures_data
    group_matches = fixtures_data["group_stage"][group_name]
    match_found = False
    for i, match in enumerate(group_matches):
        # Identify the match regardless of player order in stored list
        if (match[0] == p1_id and match[1] == p2_id):
            group_matches[i] = [p1_id, p2_id, score1, score2]
            match_found = True
            break
        elif (match[0] == p2_id and match[1] == p1_id): # If players were swapped in storage
            group_matches[i] = [p2_id, p1_id, score2, score1] # Ensure consistent storage order
            match_found = True
            break

    if not match_found:
        await update.message.reply_text("❌ Error: Group match not found in fixtures.")
        return

    # Update player stats
    player1_stats = players[p1_id]["stats"]
    player2_stats = players[p2_id]["stats"]

    player1_stats["gf"] += score1
    player1_stats["ga"] += score2
    player1_stats["gd"] = player1_stats["gf"] - player1_stats["ga"]

    player2_stats["gf"] += score2
    player2_stats["ga"] += score1
    player2_stats["gd"] = player2_stats["gf"] - player2_stats["ga"]

    if score1 > score2:
        player1_stats["wins"] += 1
        player1_stats["points"] += 3
        player2_stats["losses"] += 1
        winner_name = players[p1_id]['team']
    elif score2 > score1:
        player2_stats["wins"] += 1
        player2_stats["points"] += 3
        player1_stats["losses"] += 1
        winner_name = players[p2_id]['team']
    else: # Draw
        player1_stats["draws"] += 1
        player1_stats["points"] += 1
        player2_stats["draws"] += 1
        player2_stats["points"] += 1
        winner_name = "Draw"

    players[p1_id]["stats"] = player1_stats
    players[p2_id]["stats"] = player2_stats

    save_state("fixtures", fixtures_data)
    save_state("players", players)

    await update.message.reply_text(f"✅ Score {score1}-{score2} recorded for {players[p1_id]['team']} vs {players[p2_id]['team']}. Winner: {winner_name}.")
    await context.bot.send_message(GROUP_ID, f"⚽️ *Group Match Result:* {players[p1_id]['team']} {score1} - {score2} {players[p2_id]['team']}\n_Check /standings for updates._", parse_mode='Markdown')

    # Check if all group matches are done
    all_group_matches_completed = True
    for group_matches_list in fixtures_data["group_stage"].values():
        for match in group_matches_list:
            if match[2] is None: # If score is null
                all_group_matches_completed = False
                break
        if not all_group_matches_completed:
            break

    if all_group_matches_completed:
        await context.bot.send_message(ADMIN_ID, "All group stage matches completed! Initiating knockout stage.")
        await advance_to_knockout(context)


async def advance_to_knockout(context: ContextTypes.DEFAULT_TYPE):
    players = load_state("players")
    groups_data = load_state("groups")
    fixtures_data = load_state("fixtures")
    tournament_state = load_state("tournament_state")

    all_qualified_sorted = [] # Stores (player_id, player_info) for top 2 from each group, sorted for pairing

    for group_name in sorted(groups_data.keys()): # Iterate through groups in alphabetical order
        group_players_ids = groups_data[group_name]

        standings_for_group = []
        for p_id in group_players_ids:
            player_info = players.get(p_id)
            if player_info:
                standings_for_group.append((p_id, player_info))

        # Sort group teams by points, then GD, then GF (descending)
        standings_for_group.sort(key=lambda x: (x[1]['stats']['points'], x[1]['stats']['gd'], x[1]['stats']['gf']), reverse=True)

        # Add top 2 players from this group to the overall sorted list
        all_qualified_sorted.extend(standings_for_group[:2])

    # Now all_qualified_sorted should be:
    # [1A, 2A, 1B, 2B, 1C, 2C, 1D, 2D, 1E, 2E, 1F, 2F, 1G, 2G, 1H, 2H]

    r16_matchups = []
    # Standard World Cup R16 pairings based on group positions
    # 1A vs 2B, 1C vs 2D, 1E vs 2F, 1G vs 2H
    # 1B vs 2A, 1D vs 2C, 1F vs 2E, 1H vs 2G

    # Indices in all_qualified_sorted:
    # 0=1A, 1=2A, 2=1B, 3=2B, 4=1C, 5=2C, 6=1D, 7=2D,
    # 8=1E, 9=2E, 10=1F, 11=2F, 12=1G, 13=2G, 14=1H, 15=2H

    r16_matchups.append([all_qualified_sorted[0][0], all_qualified_sorted[3][0], None, None]) # 1A vs 2B
    r16_matchups.append([all_qualified_sorted[4][0], all_qualified_sorted[7][0], None, None]) # 1C vs 2D
    r16_matchups.append([all_qualified_sorted[8][0], all_qualified_sorted[11][0], None, None]) # 1E vs 2F
    r16_matchups.append([all_qualified_sorted[12][0], all_qualified_sorted[15][0], None, None]) # 1G vs 2H

    r16_matchups.append([all_qualified_sorted[2][0], all_qualified_sorted[1][0], None, None]) # 1B vs 2A
    r16_matchups.append([all_qualified_sorted[6][0], all_qualified_sorted[5][0], None, None]) # 1D vs 2C
    r16_matchups.append([all_qualified_sorted[10][0], all_qualified_sorted[9][0], None, None]) # 1F vs 2E
    r16_matchups.append([all_qualified_sorted[14][0], all_qualified_sorted[13][0], None, None]) # 1H vs 2G

    fixtures_data["round_of_16"] = r16_matchups
    tournament_state["stage"] = "round_of_16"
    save_state("fixtures", fixtures_data)
    save_state("tournament_state", tournament_state)

    await context.bot.send_message(GROUP_ID, "🎉 Group Stage is over! The Knockout Stage (Round of 16) has begun!\nCheck /fixtures for the new matchups!")
    await notify_knockout_matches(context, "round_of_16")

async def notify_knockout_matches(context: ContextTypes.DEFAULT_TYPE, stage: str):
    fixtures_data = load_state("fixtures")
    players_data = load_state("players")

    matches = fixtures_data.get(stage, [])
    if not matches:
        return

    message = f"📢 *{stage.replace('_', ' ').title()} Matches:*\n\n"
    for match in matches:
        p1_id, p2_id, _, _ = match
        p1_info = players_data.get(p1_id)
        p2_info = players_data.get(p2_id)
        if p1_info and p2_info:
            message += f"{p1_info['team']} (@{p1_info['username']}) vs {p2_info['team']} (@{p2_info['username']})\n"
    message += "\n_Good luck to all participants!_"
    await context.bot.send_message(GROUP_ID, message, parse_mode='Markdown')


async def handle_knockout_score(update: Update, context: ContextTypes.DEFAULT_TYPE, stage: str, p1_id: str, p2_id: str, score1: int, score2: int):
    fixtures_data = load_state("fixtures")
    players_data = load_state("players")
    tournament_state = load_state("tournament_state")

    if score1 == score2:
        await update.message.reply_text("❌ Knockout matches cannot be a draw. Please enter a decisive score.")
        return

    winner_id = p1_id if score1 > score2 else p2_id
    loser_id = p2_id if score1 > score2 else p1_id

    # Update score in current stage's fixtures
    current_matches = fixtures_data.get(stage, [])
    match_found_and_updated = False
    for i, match in enumerate(current_matches):
        if (match[0] == p1_id and match[1] == p2_id):
            current_matches[i] = [p1_id, p2_id, score1, score2]
            match_found_and_updated = True
            break
        elif (match[0] == p2_id and match[1] == p1_id): # If players were swapped in storage
            current_matches[i] = [p2_id, p1_id, score2, score1] # Ensure consistent storage order
            match_found_and_updated = True
            break

    if not match_found_and_updated:
        await update.message.reply_text("❌ Error: Knockout match not found or already processed in fixtures.")
        return

    fixtures_data[stage] = current_matches
    save_state("fixtures", fixtures_data)

    winner_info = players_data.get(winner_id)
    loser_info = players_data.get(loser_id)

    await update.message.reply_text(f"✅ Score {score1}-{score2} recorded for {winner_info['team']} vs {loser_info['team']}. {winner_info['team']} advances!")
    await context.bot.send_message(GROUP_ID, f"🔥 *Knockout Result ({stage.replace('_', ' ').title()}):* {winner_info['team']} {score1} - {score2} {loser_info['team']}\n*{winner_info['team']}* advances!", parse_mode='Markdown')


    # Check if all matches in current stage are completed
    all_matches_completed = True
    for match in current_matches:
        if match[2] is None: # If score is null
            all_matches_completed = False
            break

    if all_matches_completed:
        # Advance to next stage
        next_stage = ""
        if stage == "round_of_16":
            next_stage = "quarter_finals"
        elif stage == "quarter_finals":
            next_stage = "semi_finals"
        elif stage == "semi_finals":
            next_stage = "final"
        elif stage == "final":
            next_stage = "completed"

        if next_stage == "completed":
            await context.bot.send_message(GROUP_ID, f"🏆 Tournament Concluded! The Champion is {winner_info['team']} (@{winner_info['username']})!")
            tournament_state["stage"] = "completed"
            save_state("tournament_state", tournament_state)
            return

        # Prepare for next stage: collect winners
        winners_of_current_stage = []
        for match in current_matches:
            if match[2] is not None and match[3] is not None:
                winner = match[0] if match[2] > match[3] else match[1]
                winners_of_current_stage.append(winner)

        random.shuffle(winners_of_current_stage) # Shuffle for new pairings (important for quarterfinals/semis)

        next_stage_fixtures = []
        for i in range(0, len(winners_of_current_stage), 2):
            if i + 1 < len(winners_of_current_stage): # Ensure pairs
                next_stage_fixtures.append([winners_of_current_stage[i], winners_of_current_stage[i+1], None, None])
            else:
                print(f"Warning: Odd number of winners for {next_stage}. This should not happen in a perfect bracket.")
                # Handle bye, or error, depending on tournament size

        fixtures_data[next_stage] = next_stage_fixtures
        tournament_state["stage"] = next_stage
        save_state("fixtures", fixtures_data)
        save_state("tournament_state", tournament_state)

        await context.bot.send_message(GROUP_ID, f"🥳 All {stage.replace('_', ' ').title()} matches completed! Advancing to {next_stage.replace('_', ' ').title()}!")
        await notify_knockout_matches(context, next_stage)


async def reset_tournament(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Only the admin can reset the tournament.")
        return

    # Clear all relevant data from Firebase
    save_state("players", {})
    save_state("groups", {})
    save_state("fixtures", {})
    save_state("lock", {})
    save_state("tournament_state", {"stage": "registration"})
    save_state("rules_list", []) # Clear rules as well

    await update.message.reply_text("✅ Tournament data has been reset. Registrations are now open.")
    await context.bot.send_message(GROUP_ID, "📢 The tournament has been reset by the admin. Registrations are now open! Use /register to join.")


# --- Main function for webhook setup ---
async def start_webhook_app():
    # ... (your existing imports and Flask app setup) ...

async def start_webhook_app():
    print("--- DEBUG: start_webhook_app function entered ---") # ADD THIS LINE

    # The webhook URL provided by Railway.app (e.g., https://<YOUR_RAILWAY_APP_DOMAIN>.railway.app)
    WEBHOOK_URL = os.environ.get("WEBHOOK_URL") 
    PORT = int(os.environ.get("PORT", 8080))

    if not BOT_TOKEN:
        print("Error: BOT_TOKEN environment variable not set.")
        return
    if not WEBHOOK_URL:
        print("Error: WEBHOOK_URL environment variable not set. This is crucial for Railway deployment.")
        # Fallback to polling for local development if WEBHOOK_URL is not set
        print("Falling back to polling for local development. For Railway, set WEBHOOK_URL.")
        # ... (rest of your polling fallback code) ...

    # Initialize Firebase connection before running the bot
    init_firebase()
    if firebase_db_ref is None: # If Firebase connection failed, stop
        print("Fatal: Could not initialize Firebase. Exiting.") # YOU SHOULD SEE THIS IF FIREBASE FAILS
        return

    print("--- DEBUG: Firebase initialized, proceeding with webhook setup ---") # ADD THIS LINE IF FIREBASE SUCCEEDS

    # ... (rest of your webhook setup code) ...
    # The webhook URL provided by Railway.app (e.g., https://<YOUR_RAILWAY_APP_DOMAIN>.railway.app)
    WEBHOOK_URL = os.environ.get("WEBHOOK_URL") 
    PORT = int(os.environ.get("PORT", 8080))

    if not BOT_TOKEN:
        print("Error: BOT_TOKEN environment variable not set.")
        return
    if not WEBHOOK_URL:
        print("Error: WEBHOOK_URL environment variable not set. This is crucial for Railway deployment.")
        # Fallback to polling for local development if WEBHOOK_URL is not set
        print("Falling back to polling for local development. For Railway, set WEBHOOK_URL.")
        application = ApplicationBuilder().token(BOT_TOKEN).build()
        await set_bot_commands(application) # Await the async function

        # Add all handlers
        conv_handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(handle_team_selection, pattern=r"^team_select:")],
            states={
                REGISTER_PES: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_pes_name)],
            },
            fallbacks=[CommandHandler('cancel', cancel_registration)],
            allow_reentry=True
        )
        application.add_handler(CommandHandler('start', start))
        application.add_handler(CommandHandler('register', register))
        application.add_handler(CommandHandler('fixtures', fixtures))
        application.add_handler(CommandHandler('standings', group_standings))
        application.add_handler(CommandHandler('addscore', addscore))
        application.add_handler(MessageHandler(filters.Regex(r"^/match[0-9]+ "), handle_score))
        application.add_handler(CommandHandler("addrule", addrule))
        application.add_handler(CommandHandler("rules", rules))
        application.add_handler(CommandHandler("players", players_list))
        application.add_handler(CommandHandler("start_tournament", start_tournament))
        application.add_handler(CommandHandler("reset_tournament", reset_tournament))
        application.add_handler(conv_handler)

        application.run_polling()
        return

    # Initialize Firebase connection before running the bot
    init_firebase()
    if firebase_db_ref is None: # If Firebase connection failed, stop
        print("Fatal: Could not initialize Firebase. Exiting.")
        return

    # Use python-telegram-bot's webhook functionalities
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Set commands only once at startup
    await set_bot_commands(application) # Await the async function

    # Conversation Handler for Registration
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_team_selection, pattern=r"^team_select:")],
        states={
            REGISTER_PES: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_pes_name)],
        },
        fallbacks=[CommandHandler('cancel', cancel_registration)],
        allow_reentry=True
    )

    # Register handlers
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('register', register))
    application.add_handler(CommandHandler('fixtures', fixtures))
    application.add_handler(CommandHandler('standings', group_standings))
    application.add_handler(CommandHandler('addscore', addscore))
    application.add_handler(MessageHandler(filters.Regex(r"^/match[0-9]+ "), handle_score))
    application.add_handler(CommandHandler("addrule", addrule))
    application.add_handler(CommandHandler("rules", rules))
    application.add_handler(CommandHandler("players", players_list))
    application.add_handler(CommandHandler("start_tournament", start_tournament))
    application.add_handler(CommandHandler("reset_tournament", reset_tournament))
    application.add_handler(conv_handler)

    # Start the Flask app in a separate thread.
    # This thread will serve the root URL '/' for health checks,
    # but the Telegram bot's `run_webhook` will handle the '/webhook' path.
    keep_alive_flask()

    # Start the webhook
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="", # This means the webhook URL is base URL + /
        webhook_url=WEBHOOK_URL # The full URL Telegram sends updates to (Railway provides this)
    )

if __name__ == '__main__':
    # Run the async main function
    asyncio.run(start_webhook_app())
