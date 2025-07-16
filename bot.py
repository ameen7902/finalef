import json
import time
import random
import os
from collections import defaultdict
import base64 # For decoding Firebase key
import asyncio # Keep for async operations
from telegram.ext import CallbackQueryHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application,
    CommandHandler, CallbackQueryHandler, MessageHandler, filters,
    ContextTypes, ConversationHandler
)
# REMOVED: from flask import Flask, request, jsonify # No longer needed
import re # Make sure this is at the top of your file
from telegram.constants import ParseMode
# Firebase Imports
import firebase_admin
from firebase_admin import credentials, db

# === CONFIG ===
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROUP_ID = int(os.environ.get("GROUP_ID", -1002835703789)) # Default a placeholder ID
ADMIN_ID = int(os.environ.get("ADMIN_ID", 7366894756)) # Default a placeholder ID

# Firebase Configuration
FIREBASE_SERVICE_ACCOUNT_B64 = os.environ.get("FIREBASE_SERVICE_ACCOUNT_B64")
FIREBASE_DATABASE_URL = os.environ.get("FIREBASE_DATABASE_URL")

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
        # We will not exit here, but allow the bot to start if not using DB features
        return

    try:
        service_account_info_bytes = base64.b64decode(FIREBASE_SERVICE_ACCOUNT_B64)
        service_account_info = json.loads(service_account_info_bytes)

        cred = credentials.Certificate(service_account_info)
        firebase_admin.initialize_app(cred, {
            'databaseURL': FIREBASE_DATABASE_URL
        })
        firebase_db_ref = db.reference('/')
        print("Successfully initialized Firebase!")
    except Exception as e:
        print(f"Error initializing Firebase: {e}")
        firebase_db_ref = None

def load_state(key, default_value=None):
    if not firebase_db_ref:
        print(f"Firebase not initialized for key {key}. Returning default.")
        return default_value if default_value is not None else {}
    try:
        data = firebase_db_ref.child(key).get()
        if data is None:
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
        firebase_db_ref.child(key).set(data)
    except Exception as e:
        print(f"Error saving data to Firebase for key {key}: {e}")

# === LOCKING SYSTEM (now in Firebase) ===
def is_locked():
    lock = load_state("lock")
    if not lock:
        return False
    if time.time() - lock.get("start_time", 0) > 300: # 5 minutes timeout
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
    rules_list = load_state("rules_list", default_value=[])
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
    if row:
        keyboard.append(row)
    return keyboard

async def handle_team_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    if user.id != get_locked_user():
        await query.edit_message_text("⚠️ You are not allowed to register now. Please wait your turn.")
        return ConversationHandler.END

    team_full_name = query.data.split(':', 1)[1]
    set_selected_team(team_full_name)

    await query.edit_message_text(f"✅ Team selected: {team_full_name}\n\nNow send your PES username:")
    return REGISTER_PES
def escape_markdown_v2(text: str) -> str:
    """Helper function to escape special MarkdownV2 characters."""
    special_chars = r'_*[]()~`>#+-=|{}.!'
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text
async def receive_pes_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    pes_name = update.message.text.strip()
    players = load_state("players")

    team = get_locked_team()
    if not team:
        await update.message.reply_text("❌ Something went wrong. Try /register again.")
        unlock_user()
        return ConversationHandler.END
    escaped_user_display_name = escape_markdown_v2(user.username)
    escaped_team_name = escape_markdown_v2(team)
    players[str(user.id)] = {
        "name": user.first_name,
        "username": user.username or "NoUsername",
        "team": team,
        "pes": pes_name,
        "group": None,
        "stats": {"wins": 0, "draws": 0, "losses": 0, "gf": 0, "ga": 0, "points": 0, "gd": 0}
    }

    save_state("players", players)
    unlock_user()

    await context.bot.send_message(chat_id=user.id, text=f"✅ Registered!\n🏳️ Team: {team}\n🎮 PES: {pes_name}")
    await context.bot.send_message(
    chat_id=GROUP_ID,
    text=f"✅ It's official\! @{escaped_user_display_name}, representing **{escaped_team_name}**, has successfully qualified for the FIFA WORLD CUP 2014\!🏆⚽️",
    parse_mode=ParseMode.MARKDOWN_V2
)

    return ConversationHandler.END

async def cancel_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id == get_locked_user():
        unlock_user()
        await update.message.reply_text("❌ Registration cancelled.")
    else:
        await update.message.reply_text("ℹ️ No active registration to cancel.")
    return ConversationHandler.END

async def set_bot_commands(application_instance):
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
    await application_instance.bot.set_my_commands(commands)
    # print("Bot commands set.") # This print will now happen after the await

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
    group_names = [f"Group {chr(65 + i)}" for i in range(8)]

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
        for i in range(len(player_ids_in_group)):
            for j in range(i + 1, len(player_ids_in_group)):
                player1_id = player_ids_in_group[i]
                player2_id = player_ids_in_group[j]
                group_fixtures.append([player1_id, player2_id, None, None])
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
        if not knockout_matches:
            await update.message.reply_text("❌ Knockout matches for this stage are not yet drawn.")
            return

        reply_text += f"📅 Your Knockout Match - {player_info['team']}\n\n"
        for match in knockout_matches:
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
                break

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
    for group_name in sorted(groups_data.keys()):
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

        standings.sort(key=lambda x: (x['points'], x['gd'], x['gf']), reverse=True)

        group_text = f"📊 *{group_name} Standings:*\n"
        group_text += "Team        | Pts | GD | GF | W-D-L\n"
        group_text += "--------------------------------------\n"
        for team_stat in standings:
            team_display = (team_stat['team'] + "          ")[:10]
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


current_admin_matches = {}

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
    current_admin_matches.clear()
    idx = 1

    if current_stage == "group_stage":
        for group_name, matches in fixtures_data.get("group_stage", {}).items():
            for match in matches:
                p1_id, p2_id, score1, score2 = match
                if score1 is None:
                    p1 = players_data.get(p1_id)
                    p2 = players_data.get(p2_id)
                    if p1 and p2:
                        current_admin_matches[f"match{idx}"] = {"type": "group", "group": group_name, "p1_id": p1_id, "p2_id": p2_id}
                        reply += f"/match{idx} → {p1['team']} vs {p2['team']} ({group_name})\n"
                        idx += 1
    elif current_stage in ["round_of_16", "quarter_finals", "semi_finals", "final"]:
        for match in fixtures_data.get(current_stage, []):
            p1_id, p2_id, score1, score2 = match
            if score1 is None:
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
        return

    text = update.message.text.lower().strip()
    try:
        cmd_parts = text.split(" ", 1)
        if len(cmd_parts) < 2:
            raise ValueError("Score not provided")

        cmd, score_str = cmd_parts
        match_key = cmd[1:]
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
        p2_id = match_info["p2_id"]

        if match_type == "group":
            await handle_group_score(update, context, match_info["group"], p1_id, p2_id, score1, score2)
        elif match_type == "knockout":
            await handle_knockout_score(update, context, match_info["stage"], p1_id, p2_id, score1, score2)

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

    group_matches = fixtures_data["group_stage"][group_name]
    match_found = False
    for i, match in enumerate(group_matches):
        if (match[0] == p1_id and match[1] == p2_id):
            group_matches[i] = [p1_id, p2_id, score1, score2]
            match_found = True
            break
        elif (match[0] == p2_id and match[1] == p1_id):
            group_matches[i] = [p2_id, p1_id, score2, score1]
            match_found = True
            break

    if not match_found:
        await update.message.reply_text("❌ Error: Group match not found in fixtures.")
        return

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
    else:
        player1_stats["draws"] += 1
        player1_stats["points"] += 1
        player2_stats["draws"] += 1
        player2_stats["points"] += 1
        winner_name = "Draw"

    players[p1_id]["stats"] = player1_stats
    players[p2_id]["stats"] = player2_2stats

    save_state("fixtures", fixtures_data)
    save_state("players", players)

    await update.message.reply_text(f"✅ Score {score1}-{score2} recorded for {players[p1_id]['team']} vs {players[p2_id]['team']}. Winner: {winner_name}.")
    await context.bot.send_message(GROUP_ID, f"⚽️ *Group Match Result:* {players[p1_id]['team']} {score1} - {score2} {players[p2_id]['team']}\n_Check /standings for updates._", parse_mode='Markdown')

    all_group_matches_completed = True
    for group_matches_list in fixtures_data["group_stage"].values():
        for match in group_matches_list:
            if match[2] is None:
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

    all_qualified_sorted = []

    for group_name in sorted(groups_data.keys()):
        group_players_ids = groups_data[group_name]

        standings_for_group = []
        for p_id in group_players_ids:
            player_info = players.get(p_id)
            if player_info:
                standings_for_group.append((p_id, player_info))

        standings_for_group.sort(key=lambda x: (x[1]['stats']['points'], x[1]['stats']['gd'], x[1]['stats']['gf']), reverse=True)

        all_qualified_sorted.extend(standings_for_group[:2])

    r16_matchups = []
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

    current_matches = fixtures_data.get(stage, [])
    match_found_and_updated = False
    for i, match in enumerate(current_matches):
        if (match[0] == p1_id and match[1] == p2_id):
            current_matches[i] = [p1_id, p2_id, score1, score2]
            match_found_and_updated = True
            break
        elif (match[0] == p2_id and match[1] == p1_id):
            current_matches[i] = [p2_id, p1_id, score2, score1]
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

    all_matches_completed = True
    for match in current_matches:
        if match[2] is None:
            all_matches_completed = False
            break

    if all_matches_completed:
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

        winners_of_current_stage = []
        for match in current_matches:
            if match[2] is not None and match[3] is not None:
                winner = match[0] if match[2] > match[3] else match[1]
                winners_of_current_stage.append(winner)

        random.shuffle(winners_of_current_stage)

        next_stage_fixtures = []
        for i in range(0, len(winners_of_current_stage), 2):
            if i + 1 < len(winners_of_current_stage):
                next_stage_fixtures.append([winners_of_current_stage[i], winners_of_current_stage[i+1], None, None])
            else:
                print(f"Warning: Odd number of winners for {next_stage}. This should not happen in a perfect bracket.")

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

    save_state("players", {})
    save_state("groups", {})
    save_state("fixtures", {})
    save_state("lock", {})
    save_state("tournament_state", {"stage": "registration"})
    save_state("rules_list", [])

    await update.message.reply_text("✅ Tournament data has been reset. Registrations are now open.")
    await context.bot.send_message(GROUP_ID, "📢 The tournament has been reset by the admin. Registrations are now open! Use /register to join.")


# --- PTB Application Setup and Run Logic ---

# We define the application instance globally or pass it.
# The previous partial `main` function was causing a conflict.
# ... (all your existing code, imports, functions, handlers etc. above this point) ...

# Global application instance (initialized to None)
application = None

# This function will handle all the synchronous setup of your bot
def setup_bot_handlers_sync(app_instance: Application) -> None:
    # Add handlers
    conv_handler = ConversationHandler(
    entry_points=[
        CommandHandler("register", register),
        # ADD THIS LINE:
        CallbackQueryHandler(handle_team_selection, pattern=r"^team_select:")
    ],
    states={
        REGISTER_PES: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_pes_name)],
    },
    fallbacks=[CommandHandler("cancel", cancel_registration)],
    )
    app_instance.add_handler(conv_handler)

    app_instance.add_handler(CommandHandler("start", start))
    app_instance.add_handler(CommandHandler("rules", rules))
    app_instance.add_handler(CommandHandler("players", players_list))
    app_instance.add_handler(CommandHandler("addrule", addrule))
    app_instance.add_handler(CommandHandler("start_tournament", start_tournament))
    app_instance.add_handler(CommandHandler("fixtures", fixtures))
    app_instance.add_handler(CommandHandler("standings", group_standings))

    # Dynamically add handlers for /matchX commands for admin scores
    for i in range(1, 101): # Assuming up to 100 matches
        app_instance.add_handler(CommandHandler(f"match{i}", handle_score))

    app_instance.add_handler(CommandHandler("addscore", addscore))
    app_instance.add_handler(CommandHandler("reset_tournament", reset_tournament))

    app_instance.add_handler(CallbackQueryHandler(handle_team_selection, pattern=r"^team_select:"))
    print("--- Handlers added ---") # Moved print here for clearer flow

# This is the main asynchronous function that will be executed by asyncio.run
async def run_polling_mode_bot():
    global application # Access the global application instance

    # Set bot commands (this is an async operation and must be awaited)
    print("--- Setting bot commands ---") # Debug print
    await set_bot_commands(application)
    print("--- Bot commands set ---") # Debug print
    
    print("--- Starting bot in polling mode ---")
    # This call will block and run the bot's polling loop indefinitely
    await application.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
    print("--- Bot polling finished (should not be reached during normal operation) ---") # Debug print

# Main entry point for the script
if __name__ == '__main__':
    print("--- Script execution started ---")

    try:
        # Initialize Firebase once (synchronous)
        print("--- Initializing Firebase ---")
        init_firebase()
        if firebase_db_ref is None:
            print("FATAL: Firebase could not be initialized or required ENV vars are missing. Exiting.")
            import sys
            sys.exit(1)
        print("--- Firebase initialization status: OK ---")

        # Basic check for BOT_TOKEN as it's fundamental
        if not BOT_TOKEN:
            print("FATAL: BOT_TOKEN environment variable not set. Cannot run bot. Exiting.")
            import sys
            sys.exit(1)
        print("--- BOT_TOKEN is set ---")

        print("--- Building Telegram Application instance ---")

        # Define an async function to be run after Application init, but before polling starts
        async def post_init_setup(app_instance: Application) -> None:
            print("--- Post-init setup: Setting bot commands ---")
            # Call your existing async function here, passing the application instance
            await set_bot_commands(app_instance)
            print("--- Post-init setup: Bot commands set ---")


        # Build the Application instance, using post_init to set commands
        application = Application.builder().token(BOT_TOKEN).post_init(post_init_setup).build()
        print("--- Telegram Application instance built ---")

        # Set up all synchronous handlers and other configuration
        print("--- Setting up bot handlers ---")
        setup_bot_handlers_sync(application)
        print("--- Bot handlers setup complete ---")

        # Now, run the main polling part. PTB's run_polling manages its own event loop.
        print("--- Starting bot in polling mode ---")
        application.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
        print("--- Bot polling stopped (this should not print unless bot gracefully exits) ---")


    except Exception as e:
        print(f"\n!!! CRITICAL UNHANDLED EXCEPTION DURING BOT STARTUP !!!")
        print(f"Error type: {type(e).__name__}")
        print(f"Error message: {e}")
        import traceback
        traceback.print_exc()
        print("--- Script terminated due to unhandled exception ---")
        import sys
        sys.exit(1)

    print("--- End of script execution path ---")

