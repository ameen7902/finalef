import json
import time
import random
import re
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
from collections import defaultdict # Ensure this is imported
import itertools # Add this import for combination generation
import html # <--- ADD THIS IMPORT at the top of your bot.py file
from telegram.constants import ParseMode
# Firebase Imports
import firebase_admin
from firebase_admin import credentials, db
current_admin_matches = {} # Dictionary to store matches accessible by /matchX commands
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
def escape_markdown_v2(text: str) -> str:
    """Helper function to escape markdown v2 special characters."""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(r'([%s])' % re.escape(escape_chars), r'\\\1', text)
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



async def receive_pes_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    pes_name = update.message.text.strip()
    players = load_state("players")

    team = get_locked_team()
    if not team:
        await update.message.reply_text("❌ Something went wrong. Try /register again.")
        unlock_user()
        return ConversationHandler.END

    # --- THESE LINES ARE CRUCIAL AND MUST BE HERE ---
    user_display_name = user.username or user.first_name # <-- This line defines user_display_name
    html_escaped_user_display_name = html.escape(user_display_name)
    html_escaped_team_name = html.escape(team) # team is already defined above, so we can escape it
    # -------------------------------------------------

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

    # Message to the user's DM (removed duplicate)
    await context.bot.send_message(
        chat_id=user.id,
        text=f"✅ Registered!\n🏳️ Team: {team}\n🎮 PES: {pes_name}"
    )

    # --- Construct the group message using HTML ---
    # The entire message will be bold because of the outer <b> tags.
    group_message_text = (
        f"<b>✅ It's official! @{html_escaped_user_display_name}, representing {html_escaped_team_name}, "
        f"has successfully qualified for the FIFA WORLD CUP 2014!🏆⚽️</b>"
    )
    await context.bot.send_message(
        chat_id=GROUP_ID,
        text=group_message_text,
        parse_mode=ParseMode.HTML # <--- IMPORTANT: This is ParseMode.HTML
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
def make_group_fixtures(groups: dict):
    group_stage_fixtures = {}

    for group_name, player_ids_in_group in groups.items():
        group_fixtures = []
        for i in range(len(player_ids_in_group)):
            for j in range(i + 1, len(player_ids_in_group)):
                player1_id = player_ids_in_group[i]
                player2_id = player_ids_in_group[j]
                # Each match is explicitly a 4-element list: [player1_id, player2_id, score1, score2]
                group_fixtures.append([player1_id, player2_id, None, None])
        group_stage_fixtures[group_name] = group_fixtures

    return group_stage_fixtures

# --- MODIFIED make_groups FUNCTION ---
# This function now only assigns players to groups and saves 'players' and 'groups' state.
# It returns the 'groups' data, and DOES NOT call make_group_fixtures.
async def make_groups(context: ContextTypes.DEFAULT_TYPE):
    players = load_state("players") # Load players to update their groups
    player_ids = list(players.keys())
    random.shuffle(player_ids)

    groups = defaultdict(list)
    group_names = [f"Group {chr(65 + i)}" for i in range(8)]

    for i, player_id in enumerate(player_ids):
        group_name = group_names[i % 8]
        groups[group_name].append(player_id)
        players[player_id]['group'] = group_name

    save_state("players", players)
    # Save groups as a regular dict, not defaultdict
    save_state("groups", {name: ids for name, ids in groups.items()})

    # This function now RETURNS the groups. It does NOT call make_group_fixtures.
    return {name: ids for name, ids in groups.items()} # Return as a regular dict


# --- MODIFIED start_tournament HANDLER ---
# This function orchestrates everything, gets data from helper functions,
# and performs the FINAL save of the complete fixtures data.
# --- MODIFIED start_tournament HANDLER ---
# This function orchestrates everything, gets data from helper functions,
# and performs the FINAL save of the complete fixtures data.
async def start_tournament(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ADMIN_ID="7366894756"
    if user_id != ADMIN_ID:
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

    # 1. Make groups and get the group assignments back
    # (This make_groups function is assumed to be the one you already have that saves players and groups state)
    groups = await make_groups(context)

    # 2. Generate group fixtures using the returned groups data
    # (This make_group_fixtures now includes the round_number)
    group_stage_fixtures = make_group_fixtures(groups)

    # 3. Assemble the COMPLETE fixtures_data object
    # Initialize all stages to ensure a clean, array-friendly structure
    fixtures_data = {
        "group_stage": group_stage_fixtures,
        "round_of_16": [],   # Initialize as empty list for future knockout matches
        "quarter_finals": [],
        "semi_finals": [],
        "final": []
    }

    # === THIS IS A DEBUG PRINT - YOU CAN REMOVE IT LATER ===
    print(f"DEBUG: Fixtures data *before* saving to Firebase: {json.dumps(fixtures_data, indent=2)}")

    # === PERFORM THE SINGLE, FINAL SAVE OF THE COMPLETE FIXTURES DATA ===
    save_state("fixtures", fixtures_data)

    # 4. Update tournament state - Initialize current group match round
    tournament_state["stage"] = "group_stage"
    tournament_state["group_match_round"] = 0 # NEW: Initialize current round to 0 (Round 1 conceptually)
    save_state("tournament_state", tournament_state)

    await update.message.reply_text("🏆 Tournament has begun! Group stage fixtures generated for Round 1.")
    await context.bot.send_message(GROUP_ID, "🔢 Group fixtures generated for Round 1! Use /fixtures to see your match schedule and /standings for current rankings.")


# --- MODIFIED make_groups FUNCTION ---
# This function now only assigns players to groups and saves 'players' and 'groups' state.
# It RETURNS the 'groups' data, and DOES NOT call make_group_fixtures itself.
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
    # Save groups as a regular dict, not defaultdict
    save_state("groups", {name: ids for name, ids in groups.items()})

    # This function now RETURNS the groups. It does NOT call make_group_fixtures.
    return {name: ids for name, ids in groups.items()} # Return as a regular dict


def make_group_fixtures(groups: dict):
    fixtures_data = load_state("fixtures") # Temporarily load to potentially merge if needed, but not saving back here
    group_stage_fixtures = {}

    for group_name, player_ids_in_group in groups.items():
        # Using the new scheduler for each group
        group_fixtures = generate_round_robin_schedule(player_ids_in_group)
        group_stage_fixtures[group_name] = group_fixtures

    return group_stage_fixtures
def generate_round_robin_schedule(players_in_group):
    """
    Generates a round-robin schedule for 4 players over 3 rounds.
    Each player plays one match per round.
    Assumes len(players_in_group) is 4.
    """
    if len(players_in_group) != 4:
        # This function is specifically for groups of 4. Adjust if group sizes vary.
        # For other sizes, a different scheduling algorithm would be needed.
        print(f"WARNING: Group size is not 4. Cannot generate round-robin schedule for: {players_in_group}")
        return []

    schedule = []
    # Simplified round-robin pairings for 4 players (0, 1, 2, 3)
    # This ensures each player has one match per round.
    # Player indices refer to their position in the players_in_group list
    pairings_per_round = [
        [(0, 3), (1, 2)], # Round 0: P0 vs P3, P1 vs P2
        [(0, 2), (3, 1)], # Round 1: P0 vs P2, P3 vs P1
        [(0, 1), (2, 3)]  # Round 2: P0 vs P1, P2 vs P3
    ]

    for round_num, round_pairings in enumerate(pairings_per_round):
        for p_idx1, p_idx2 in round_pairings:
            player1_id = players_in_group[p_idx1]
            player2_id = players_in_group[p_idx2]
            # Match format: [player1_id, player2_id, score1, score2, round_number]
            schedule.append([player1_id, player2_id, None, None, round_num])
    return schedule
import json # Ensure this is at the top of your bot.py file for any debugging prints
# No 'import re' needed if you are not using the escape_markdown_v2 function.
# If you still want the BadRequest fix, you should manually re-add the
# escape_markdown_v2 function and calls, and the import re.
# For now, we're focusing purely on the round-based display.


async def fixtures(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    print(f"DEBUG: Fixtures command received from user_id: {user_id}")

    players = load_state("players")
    fixtures_data = load_state("fixtures")
    tournament_state = load_state("tournament_state")
    current_stage = tournament_state.get("stage", "registration")
    current_group_round = tournament_state.get("group_match_round", 0) # NEW: Get current round

    if user_id not in players:
        await update.message.reply_text("❌ You are not registered for the tournament. Use /register.")
        print(f"DEBUG: User {user_id} not in players.")
        return

    player_info = players[user_id]
    print(f"DEBUG: Player info for {user_id}: {player_info}")
    reply_text = ""
    found_fixture = False

    if current_stage == "group_stage":
        player_group = player_info.get("group")
        print(f"DEBUG: User {user_id}'s group: {player_group}")

        if not player_group or player_group not in fixtures_data.get("group_stage", {}):
            await update.message.reply_text("❌ Your group fixtures are not yet available.")
            print(f"DEBUG: Group '{player_group}' not found in fixtures_data group_stage.")
            return

        reply_text += f"📅 Your Group Matches - {escape_markdown_v2(player_info['team'])} ({escape_markdown_v2(player_info['group'] or 'No Group')}) - Match {current_group_round + 1}\n\n"
        group_matches = fixtures_data["group_stage"][player_group]
        print(f"DEBUG: Group '{player_group}' matches loaded: {json.dumps(group_matches, indent=2)}")

        if not group_matches:
            print(f"DEBUG: Group '{player_group}' has no matches listed.")

        for match_index, match in enumerate(group_matches):
            # Ensure match has enough elements (now 5 for round_number)
            if not isinstance(match, list) or len(match) < 5: # Changed from < 2 or < 4 to < 5
                print(f"WARNING: Malformed match data (too short) in Firebase for Group {player_group}, Match index {match_index}: {match}")
                continue

            # NEW: Check if this match belongs to the current round
            if match[4] != current_group_round: # match[4] is the round_number
                print(f"DEBUG: Skipping match {match_index} as it belongs to round {match[4]}, not current round {current_group_round}.")
                continue # Skip to the next match if not for the current round

            print(f"DEBUG: Processing match {match_index}: {match}")
            if user_id in match[0:2]:
                opponent_id = match[1] if match[0] == user_id else match[0]
                print(f"DEBUG: Found user {user_id} in match {match_index}. Opponent ID: {opponent_id}")
                opponent_info = players.get(opponent_id)
                print(f"DEBUG: Opponent info for {opponent_id}: {opponent_info}")

                if opponent_info:
                    # Check if match has score placeholders before accessing them
                    # Note: Scores are still match[2] and match[3]
                    if match[2] is not None and match[3] is not None:
                        score_status = f"({match[2]}-{match[3]})"
                        # If match is finished, display as scoreboard
                        reply_text += (
                            f"🏆 Match Result (Round {match[4] + 1}):\n"
                            f"*{escape_markdown_v2(player_info['team'])} {match[2]} - {match[3]} {escape_markdown_v2(opponent_info['team'])}*\n"
                            f"🎮 Opponent: @{escape_markdown_v2(opponent_info['username'])}\n"
                        )
                    else:
                        score_status = "(Pending)"
                        # If match is pending
                        reply_text += (
                            f"MATCHDAY ( {match[4] + 1}):\n"
                            f"{escape_markdown_v2(player_info['team'])} vs {escape_markdown_v2(opponent_info['team'])} {score_status}\n"
                            f"🎮 Opponent: @{escape_markdown_v2(opponent_info['username'])}\n"
                        )
                    found_fixture = True
                    print(f"DEBUG: Fixture found and added to reply for {user_id}. Match: {match}")
                else:
                    print(f"DEBUG: Opponent {opponent_id} not found in 'players' data.")
            else:
                print(f"DEBUG: User {user_id} not in match {match_index}'s player IDs ({match[0]}, {match[1]}).")

        # Check if current user has any active matches for the current round
        if not found_fixture:
            # If found_fixture is still False after checking all matches in the current round,
            # it means the user has no match *for this specific round* or all their matches are complete.
            # Since we are only showing ONE match now, this needs refinement if a user
            # has multiple matches for one round (which they shouldn't with the round-robin logic).
            # For now, if no match found for this user in this round, it means they might have already
            # reported their score for this specific round, or the admin needs to advance the round.
            await update.message.reply_text("✅ Your match for this round is completed or you have no active match for the current round. Please wait for the admin to advance to the next round.")
            print(f"DEBUG: No active match found for {user_id} in Round {current_group_round}.")


    elif current_stage in ["round_of_16", "quarter_finals", "semi_finals", "final"]:
        # This part remains mostly the same for now, as we agreed to handle knockouts later.
        # You might still want to add the len(match) < 4 check here if not already.
        knockout_matches = fixtures_data.get(current_stage, [])
        if not knockout_matches:
            await update.message.reply_text("❌ Knockout matches for this stage are not yet drawn.")
            print(f"DEBUG: No knockout matches for stage {current_stage}.")
            return

        reply_text += f"📅 Your Knockout Match - {player_info['team']}\n\n"
        for match_index, match in enumerate(knockout_matches):
            if not isinstance(match, list) or len(match) < 4: # Assuming knockout matches will be 4 elements initially
                print(f"WARNING: Malformed knockout match data (too short) in Firebase for stage {current_stage}, Match index {match_index}: {match}")
                continue

            print(f"DEBUG: Processing knockout match {match_index}: {match}")
            if user_id == match[0] or user_id == match[1]:
                opponent_id = match[1] if match[0] == user_id else match[0]
                opponent_info = players.get(opponent_id)
                print(f"DEBUG: Opponent info for {opponent_id}: {opponent_info}")

                if opponent_info:
                    if match[2] is not None and match[3] is not None:
                        score_status = f"({match[2]}-{match[3]})"
                        reply_text += (
                            f"🏆 Match Result (*{current_stage.replace('_', ' ').title()}*):\n"
                            f"*{player_info['team']} {match[2]} - {match[3]} {opponent_info['team']}*\n"
                            f"🎮 Opponent: @{opponent_info['username']}\n"
                        )
                    else:
                        score_status = "(Pending)"
                        reply_text += (
                            f"📅 Your Match (*{current_stage.replace('_', ' ').title()}*):\n"
                            f"{player_info['team']} vs {opponent_info['team']} {score_status}\n"
                            f"🎮 Opponent: @{opponent_info['username']}\n"
                        )
                    found_fixture = True
                    print(f"DEBUG: Fixture found and added to reply for {user_id}.")
                else:
                    print(f"DEBUG: Opponent {opponent_id} not found in 'players' data for knockout match.")
                break
            else:
                print(f"DEBUG: User {user_id} not in knockout match {match_index}'s player IDs ({match[0]}, {match[1]}).")

    # The 'found_fixture' check at the end:
    # If found_fixture is True, it means a match (either group or knockout) was found and added to reply_text.
    # If it's False here, and it's not a group stage "no active match" case,
    # it means no match was found at all.
    if found_fixture: # This check needs to be re-evaluated if we only want one match displayed.
                      # The conditional check inside 'group_stage' block is more precise now.
        if reply_text.strip(): # Only send if there's actual text
            await update.message.reply_text(reply_text, parse_mode='Markdown')
            print(f"DEBUG: Sent fixture reply for {user_id}. Reply length: {len(reply_text)} characters.")
        else:
            # This case might happen if the inner `found_fixture` was set, but then no text was generated
            # due to some edge case, or if the user's current round match is already complete and
            # the previous if-block handled it.
            await update.message.reply_text("❌ No active match found for you at this moment. Please wait for the admin to advance the round.")
    # else: The more specific message for 'no active match in group stage' is already handled above.
    # If it falls through here, and not a group stage, then this is the fallback.
    elif not found_fixture and current_stage not in ["group_stage"]: # For knockout stages if no match found
         await update.message.reply_text("❌ No upcoming match found for you or your matches are already completed for this stage.")
         print(f"DEBUG: No fixture found for {user_id}. 'found_fixture' remained False.")
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
# --- Helper function to update player statistics after a match ---
# This assumes the score for a match is reported only once.
# Recalculating stats for overridden scores would require more complex logic.
def update_player_stats(players_data, player_id, opponent_id, player_score, opponent_score):
    # Ensure 'stats' dictionary exists for the player reporting
    if 'stats' not in players_data.get(player_id, {}):
        players_data[player_id]['stats'] = {'wins': 0, 'losses': 0, 'draws': 0, 'points': 0, 'gf': 0, 'ga': 0, 'gd': 0}

    # Ensure 'stats' dictionary exists for the opponent
    if 'stats' not in players_data.get(opponent_id, {}):
        players_data[opponent_id]['stats'] = {'wins': 0, 'losses': 0, 'draws': 0, 'points': 0, 'gf': 0, 'ga': 0, 'gd': 0}

    # Get current stats objects for easier modification
    player_stats = players_data[player_id]['stats']
    opponent_stats = players_data[opponent_id]['stats']

    # Update Goals For (GF) and Goals Against (GA) for both players
    player_stats['gf'] += player_score
    player_stats['ga'] += opponent_score
    opponent_stats['gf'] += opponent_score
    opponent_stats['ga'] += player_score

    # Update Goal Difference (GD) for both players
    player_stats['gd'] = player_stats['gf'] - player_stats['ga']
    opponent_stats['gd'] = opponent_stats['gf'] - opponent_stats['ga']

    # Update Wins, Losses, Draws, and Points for both players
    if player_score > opponent_score:
        # Player wins
        player_stats['wins'] += 1
        player_stats['points'] += 3
        opponent_stats['losses'] += 1
    elif player_score < opponent_score:
        # Opponent wins
        player_stats['losses'] += 1
        opponent_stats['wins'] += 1
        opponent_stats['points'] += 3
    else: # Draw
        # Both players draw
        player_stats['draws'] += 1
        player_stats['points'] += 1
        opponent_stats['draws'] += 1
        opponent_stats['points'] += 1

    # No need to explicitly save_state here, as the caller will save it once updates are complete.
async def addscore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global current_admin_matches # Declare global scope for modification
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized.")
        return

    fixtures_data = load_state("fixtures")
    players_data = load_state("players")
    tournament_state = load_state("tournament_state")
    current_stage = tournament_state.get("stage")
    current_group_round = tournament_state.get("group_match_round", 0) # Get current round

    if not fixtures_data or not current_stage:
        await update.message.reply_text("❌ No matches currently scheduled.")
        return

    reply = f"📋 Matches for {current_stage.replace('_', ' ').title()}:\n\n"
    current_admin_matches.clear() # Clear previous matches
    idx = 1

    if current_stage == "group_stage":
        # Admin /addscore should list only matches for the current active round
        # This makes it easier for admin to manage round by round.
        for group_name, matches in fixtures_data.get("group_stage", {}).items():
            for match in matches:
                # Ensure match has enough elements (5 for round_number) and belongs to current round
                if not isinstance(match, list) or len(match) < 5 or match[4] != current_group_round:
                    continue # Skip malformed or non-current-round matches

                p1_id, p2_id, score1, score2, round_num = match # Unpack 5 elements

                if score1 is None: # Only list pending matches
                    p1 = players_data.get(p1_id)
                    p2 = players_data.get(p2_id)
                    if p1 and p2:
                        current_admin_matches[f"match{idx}"] = {
                            "type": "group",
                            "group": group_name,
                            "p1_id": p1_id,
                            "p2_id": p2_id,
                            "round_num": round_num # Store the round number
                        }
                        # Apply escape_markdown_v2 to team names and group name
                        reply += f"/match{idx} → {escape_markdown_v2(p1['team'])} vs {escape_markdown_v2(p2['team'])} (Group {escape_markdown_v2(group_name)} - Round {round_num + 1})\n"
                        idx += 1
    elif current_stage in ["round_of_16", "quarter_finals", "semi_finals", "final"]:
        # Knockout matches still only have 4 elements [p1_id, p2_id, score1, score2]
        for match in fixtures_data.get(current_stage, []):
            # Ensure match has enough elements (4 for knockout)
            if not isinstance(match, list) or len(match) < 4:
                continue # Skip malformed matches

            p1_id, p2_id, score1, score2 = match[:4] # Unpack up to 4 elements
            if score1 is None:
                p1 = players_data.get(p1_id)
                p2 = players_data.get(p2_id)
                if p1 and p2:
                    current_admin_matches[f"match{idx}"] = {"type": "knockout", "stage": current_stage, "p1_id": p1_id, "p2_id": p2_id}
                    # Apply escape_markdown_v2 to team names and stage name
                    reply += f"/match{idx} → {escape_markdown_v2(p1['team'])} vs {escape_markdown_v2(p2['team'])} ({escape_markdown_v2(current_stage.replace('_', ' ').title())})\n"
                    idx += 1

    if not current_admin_matches:
        reply = "✅ All matches for the current round/stage are completed."
        if current_stage == "group_stage":
            reply += f"\nAdmin can now use /advance_group_round to proceed."
        elif current_stage == "group_stage_completed":
            reply += f"\nGroup stage is finished. Admin needs to draw knockout stages."

    reply += "\nTo add score: /match1 2-1"
    await update.message.reply_text(reply, parse_mode='Markdown') # Use Markdown here

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
            round_num = match_info.get("round_num") # Get round_num for group matches
            if round_num is None: # Should not happen if addscore is updated correctly
                await update.message.reply_text("❌ Error: Group match information is missing round number.")
                return
            await handle_group_score(update, context, match_info["group"], p1_id, p2_id, score1, score2, round_num) # Pass round_num
        elif match_type == "knockout":
            await handle_knockout_score(update, context, match_info["stage"], p1_id, p2_id, score1, score2)

        if match_key in current_admin_matches:
            del current_admin_matches[match_key]

    except ValueError as ve:
        await update.message.reply_text(f"❌ Invalid format. Use like: /match2 1-0. Error: {ve}")
    except Exception as e:
        print(f"Error in handle_score: {e}")
        await update.message.reply_text("❌ An unexpected error occurred.")


async def handle_group_score(update: Update, context: ContextTypes.DEFAULT_TYPE, group_name: str, p1_id: str, p2_id: str, score1: int, score2: int, round_num: int): # NEW: Added round_num parameter
    fixtures_data = load_state("fixtures")
    players = load_state("players")

    group_matches = fixtures_data["group_stage"][group_name]
    match_found = False

    for i, match in enumerate(group_matches):
        # Check if this is the correct match and belongs to the specified round
        if (match[0] == p1_id and match[1] == p2_id and match[4] == round_num):
            # Preserve round_num when updating
            group_matches[i] = [p1_id, p2_id, score1, score2, round_num]
            match_found = True
            break
        elif (match[0] == p2_id and match[1] == p1_id and match[4] == round_num):
            # Preserve round_num when updating (and swap scores for canonical order if desired, or just store as reported)
            # For consistency, it's better to store with p1_id as match[0] and p2_id as match[1]
            # even if reported by p2, then the scores swap.
            group_matches[i] = [p1_id, p2_id, score1, score2, round_num] # Correctly store in canonical order
            match_found = True
            break

    if not match_found:
        await update.message.reply_text("❌ Error: Group match not found or does not belong to the current round in fixtures.")
        return

    # Use the helper function to update player statistics
    update_player_stats(players, p1_id, p2_id, score1, score2) # p1_id and p2_id are fixed for the match

    save_state("fixtures", fixtures_data)
    save_state("players", players)

    # Use escape_markdown_v2 for team names in replies
    p1_team_name = players.get(p1_id, {}).get('team', 'Unknown Player')
    p2_team_name = players.get(p2_id, {}).get('team', 'Unknown Player')

    if score1 > score2:
        winner_name = escape_markdown_v2(p1_team_name)
    elif score2 > score1:
        winner_name = escape_markdown_v2(p2_team_name)
    else:
        winner_name = "Draw"

    await update.message.reply_text(
        f"✅ Score {score1}-{score2} recorded for {escape_markdown_v2(p1_team_name)} vs {escape_markdown_v2(p2_team_name)}. Winner: {winner_name}.",
        parse_mode='Markdown' # Ensure parse_mode is set
    )
    await context.bot.send_message(
        GROUP_ID,
        f"⚽️ *Group Match Result (Round {round_num + 1}):* {escape_markdown_v2(p1_team_name)} {score1} - {score2} {escape_markdown_v2(p2_team_name)}\n_Check /standings for updates._",
        parse_mode='Markdown'
    )

    # The logic to check if all matches are completed and advance to knockout
    # is now handled by the /advance_group_round command, NOT here.


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
# Assuming 'players' is intended to be a global variable holding your player data.
# If it's not defined globally, you might need to initialize it here:
players = {} # Or wherever you load your initial global state (e.g., players = load_state("players"))

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

        # --- IMPORTANT: Ensure 'players' data is loaded/accessible here ---
        # If 'players' is a global variable, and you load it from a file,
        # you might need to explicitly load it here if it's not already.
        # Example (uncomment if load_state sets a global 'players' variable):
        
        players = load_state("players") # Make sure your load_state function is available and returns the players dict


        # ================================================================
        # --- PLACE THE DUMMY PLAYER GENERATION CODE BLOCK HERE ---
        # ================================================================
        # This code will now be correctly indented inside the 'try' block
        # and after your application is built.

        if os.environ.get("TEST_MODE") == "true":
            print("DEBUG: TEST_MODE is ON. Injecting dummy players for tournament simulation.")

            # Optional: Clear existing players if you want a fresh start with only dummy data
            # DANGER: If you uncomment the line below and run it with your LIVE bot,
            # it WILL WIPE OUT any actual registered players' data!
            # players = {} # Uncomment with extreme caution for testing

            # --- Generate 32 Dummy Players Using a Loop ---
            num_dummy_players = 31 # Set the desired number of dummy players
            for i in range(1, num_dummy_players + 1):
                player_id = 1000 + i # Unique dummy user ID (e.g., 1001, 1002, ...)
                player_name = f"Test Player {chr(64 + i)}" if i <= 26 else f"Test Player {i}" # A, B, C... or just numbers
                username = f"tester_{player_id}"
                team_name = f"Team {chr(64 + i)} FC" if i <= 26 else f"Team {i} FC" # Unique team names
                pes_name = f"PES_User_{player_id}"

                players[str(player_id)] = {
                    "user_id": player_id,
                    "name": player_name,
                    "username": username,
                    "team": team_name,
                    "pes": pes_name,
                    "group": None, # Will be filled by create_groups
                    "stats": {"wins": 0, "draws": 0, "losses": 0, "gf": 0, "ga": 0, "points": 0, "gd": 0}
                }
            print(f"DEBUG: {num_dummy_players} dummy players injected.")

            save_state("players", players) # This line saves the dummy players to your state file
            print("DEBUG: Dummy players saved to state.")

        # ================================================================
        # --- END OF DUMMY PLAYER GENERATION CODE ---
        # ================================================================

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
