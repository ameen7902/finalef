import json
import time
import random
import re
import os
import asyncio
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
import math
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
    # ADMIN_ID="7366894756"
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



# --- MODIFIED start_tournament HANDLER ---
# This function orchestrates everything, gets data from helper functions,
# and performs the FINAL save of the complete fixtures data.
# --- MODIFIED start_tournament HANDLER ---
# This function orchestrates everything, gets data from helper functions,
# and performs the FINAL save of the complete fixtures data.
async def start_tournament(update, context):
    user_id = str(update.effective_user.id)
    
    # Admin check
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Only the admin can start the tournament.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    players = load_state("players")
    # Player count check
    if len(players) != 32:
        await update.message.reply_text(f"❌ Need exactly 32 players to start the tournament. Currently have {len(players)}.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    tournament_state = load_state("tournament_state")
    # Tournament stage check
    if tournament_state.get("stage") != "registration":
        await update.message.reply_text("❌ The tournament has already started or is in an advanced stage. Use /reset_tournament to restart.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    # Initial message to admin (before drawing starts)
    await update.message.reply_text("🎉 The tournament is starting! Initiating live group drawing...", parse_mode=ParseMode.MARKDOWN_V2)

    # 1. Make groups and get the group assignments back IN MEMORY
    players_with_groups, groups_structure = await make_groups(context) 

    # 2. Perform the live drawing announcements based on the allocated groups
    # State (players and groups) will be saved INSIDE _perform_live_group_drawing after all announcements
    await _perform_live_group_drawing(context, players_with_groups, groups_structure)

    # 3. Generate group fixtures using the returned groups data (groups_structure is the final one)
    group_stage_fixtures = make_group_fixtures(groups_structure) # Assuming make_group_fixtures is defined elsewhere

    # 4. Assemble the COMPLETE fixtures_data object
    fixtures_data = {
        "group_stage": group_stage_fixtures,
        "round_of_16": [],    # Initialize as empty list for future knockout matches
        "quarter_finals": [],
        "semi_finals": [],
        "final": []
    }

    print(f"DEBUG: Fixtures data *before* saving to Firebase: {json.dumps(fixtures_data, indent=2)}")

    # 5. Perform the single, final save of the complete fixtures data
    save_state("fixtures", fixtures_data)

    # 6. Update tournament state - Initialize current group match round
    tournament_state["stage"] = "group_stage" # Now safe to change stage
    tournament_state["group_match_round"] = 0 
    save_state("tournament_state", tournament_state)

    # Final messages to admin and group chat after everything is done (group drawing and fixtures)
    final_message_for_admin = "✅ Group drawing complete and fixtures generated! Tournament is officially in the Group Stage!"
    await update.message.reply_text(final_message_for_admin, parse_mode=ParseMode.MARKDOWN_V2)
    
    # Send a general announcement to the group chat (if you have one)
    # if 'GROUP_ID' in globals() and GROUP_ID:
    #     await context.bot.send_message(
    #         GROUP_ID,
    #         "🏆 The tournament has officially begun! Group stage fixtures generated! Use /fixtures to see your match schedule and /mygroup for your group's details.",
    #         parse_mode=ParseMode.MARKDOWN_V2
    #     )
    
    print("DEBUG: Start tournament command finished and all final messages sent.")
async def make_groups(context):
    """
    Allocates registered players into groups in memory.
    It DOES NOT save state here.
    Returns (players_data_with_groups, groups_structure).
    """
    players = load_state("players") # Load players initially
    player_ids = list(players.keys())
    random.shuffle(player_ids)

    groups = defaultdict(list)
    group_names = [f"Group {chr(65 + i)}" for i in range(8)]

    # Temporary players_data to hold in-memory changes
    players_data_with_groups = players.copy() 

    for i, player_id in enumerate(player_ids):
        group_name = group_names[i % 8]
        groups[group_name].append(player_id)
        players_data_with_groups[player_id]['group'] = group_name # Update group in temp dict

    # Convert defaultdict to regular dict for groups before returning
    groups_structure = {name: ids for name, ids in groups.items()}

    print("DEBUG: make_groups calculated groups in memory. Not saved yet.")
    return players_data_with_groups, groups_structure

def make_group_fixtures(groups: dict):
    fixtures_data = load_state("fixtures") # Temporarily load to potentially merge if needed, but not saving back here
    group_stage_fixtures = {}

    for group_name, player_ids_in_group in groups.items():
        # Using the new scheduler for each group
        group_fixtures = generate_round_robin_schedule(player_ids_in_group)
        group_stage_fixtures[group_name] = group_fixtures

    return group_stage_fixtures

async def _perform_live_group_drawing(context, players_data, allocated_groups):
    """
    Performs the live drawing announcements with delays.
    Crucially, it SAVES the updated players and groups state AFTER all announcements.
    """
    print("DEBUG: _perform_live_group_drawing function started. Will save state at the end.")

    player_ids_for_drawing = list(players_data.keys()) # Get all player IDs from the provided players_data
    random.shuffle(player_ids_for_drawing) # Shuffle for drawing animation sequence

    initial_drawing_message = "✨ FIFA Tournament Live Drawing in progress... ✨\n\n" \
                              "Each team's group will be announced shortly\\. Stay tuned\\!"
    
    # Send initial message to admin
    await context.bot.send_message(
        chat_id=ADMIN_ID, 
        text=initial_drawing_message,
        parse_mode=ParseMode.MARKDOWN_V2
    )
    # Send initial message to public group (uncomment and set GROUP_ID if you use one)
    # if 'GROUP_ID' in globals() and GROUP_ID: # Check if GROUP_ID is defined and not None/empty
    #     await context.bot.send_message(
    #        chat_id=GROUP_ID,
    #        text=initial_drawing_message,
    #        parse_mode=ParseMode.MARKDOWN_V2
    #    )

    for i, player_id in enumerate(player_ids_for_drawing):
        player_info = players_data.get(player_id, {})
        team_name = player_info.get('team', 'N/A')
        username = player_info.get('username', 'N/A')
        assigned_group = player_info.get('group', 'N/A Group') # Get the group already assigned by make_groups (in memory)

        announcement_text = (
            f"🎉 *ANNOUNCEMENT* 🎉\n"
            f"Team *{escape_markdown_v2(team_name)}* \\(@{escape_markdown_v2(username)}\\) "
            f"has been officially allotted to *{escape_markdown_v2(assigned_group)}*\\!"
        )
        
        # Send announcement to admin
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=announcement_text,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        # Send announcement to public group (uncomment if you use one)
        # if 'GROUP_ID' in globals() and GROUP_ID:
        #    await context.bot.send_message(
        #        chat_id=GROUP_ID,
        #        text=announcement_text,
        #        parse_mode=ParseMode.MARKDOWN_V2
        #    )

        print(f"DEBUG: Announcing {team_name} in {assigned_group}.")

        if i < len(player_ids_for_drawing) - 1: 
            await asyncio.sleep(20) # 20-second delay

    # --- Crucial: SAVE STATE AFTER ALL ANNOUNCEMENTS ARE DONE ---
    save_state("players", players_data) # Save players with their new group assignments
    save_state("groups", allocated_groups) # Save the group structure
    print("DEBUG: All drawing announcements complete. Players and Groups state SAVED.")


    # Final summary message for the admin (can be customized or removed if the main start_tournament sends one)
    final_drawing_summary = "✅ Live group drawing complete! All teams have been announced."
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=final_drawing_summary,
        parse_mode=ParseMode.MARKDOWN_V2
    )
    print("DEBUG: _perform_live_group_drawing finished.")
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
    current_group_round = tournament_state.get("group_match_round", 0)

    if user_id not in players:
        await update.message.reply_text("❌ You are not registered for the tournament\. Use /register\.", parse_mode=ParseMode.MARKDOWN_V2)
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
            await update.message.reply_text("❌ Your group fixtures are not yet available\.", parse_mode=ParseMode.MARKDOWN_V2)
            print(f"DEBUG: Group '{player_group}' not found in fixtures_data group_stage.")
            return

        # Escape header elements
        escaped_player_team_header = escape_markdown_v2(player_info.get('team', ''))
        escaped_player_group_header = escape_markdown_v2(player_info.get('group', 'No Group'))
        reply_text += f"📅 Your Group Matches \- {escaped_player_team_header} \({escaped_player_group_header}\) \- Match {escape_markdown_v2(str(current_group_round + 1))}\n\n"

        group_matches = fixtures_data["group_stage"][player_group]
        print(f"DEBUG: Group '{player_group}' matches loaded: {json.dumps(group_matches, indent=2)}")

        if not group_matches:
            reply_text = "❌ No matches found for your group\."
            await update.message.reply_text(reply_text, parse_mode=ParseMode.MARKDOWN_V2)
            print(f"DEBUG: Group '{player_group}' has no matches listed.")
            return

        current_round_matches_for_user = []
        for match_index, match in enumerate(group_matches):
            if not isinstance(match, list) or len(match) < 5:
                print(f"WARNING: Malformed match data (too short) in Firebase for Group {player_group}, Match index {match_index}: {match}")
                continue

            # Check if this match belongs to the current round
            if match[4] == current_group_round: # match[4] is the round_number
                if user_id in match[0:2]:
                    current_round_matches_for_user.append(match)
                    found_fixture = True # At least one match for the user in this round

        if not current_round_matches_for_user:
            # If no matches found specifically for the user in the current round
            await update.message.reply_text("✅ Your match for this round is completed or you have no active match for the current round\. Please wait for the admin to advance to the next round\.", parse_mode=ParseMode.MARKDOWN_V2)
            print(f"DEBUG: No active match found for {user_id} in Round {current_group_round}.")
            return # Exit after sending this specific message

        # Now, iterate only through the matches relevant to the user for the current round
        for match in current_round_matches_for_user:
            opponent_id = match[1] if match[0] == user_id else match[0]
            opponent_info = players.get(opponent_id)

            if opponent_info:
                # Escape all dynamic text that goes into the reply
                escaped_player_team = escape_markdown_v2(player_info.get('team', ''))
                escaped_opponent_team = escape_markdown_v2(opponent_info.get('team', ''))
                escaped_opponent_username = escape_markdown_v2(opponent_info.get('username', ''))

                if match[2] is not None and match[3] is not None:
                    # If match is finished, display as scoreboard
                    reply_text += (
                        f"🏆 Match Result \(Round {escape_markdown_v2(str(match[4] + 1))}\):\n"
                        f"*{escaped_player_team} {match[2]} \- {match[3]} {escaped_opponent_team}*\n"
                        f"🎮 Opponent: @{escaped_opponent_username}\n\n"
                    )
                else:
                    # If match is pending
                    reply_text += (
                        f"MATCHDAY \( {escape_markdown_v2(str(match[4] + 1))}\):\n"
                        f"{escaped_player_team} vs {escaped_opponent_team} \(Pending\)\n"
                        f"🎮 Opponent: @{escaped_opponent_username}\n\n"
                    )
            else:
                print(f"DEBUG: Opponent {opponent_id} not found in 'players' data.")
                # Consider adding a message for this case if it's a common occurrence
                escaped_player_team = escape_markdown_v2(player_info.get('team', ''))
                reply_text += f"MATCHDAY \( {escape_markdown_v2(str(match[4] + 1))}\):\n" \
                              f"{escaped_player_team} vs Unknown Opponent \(Pending\)\n" \
                              f"🎮 Opponent: @unknown\n\n" # Fallback if username is missing


    elif current_stage in ["round_of_16", "quarter_finals", "semi_finals", "final"]:
        knockout_matches = fixtures_data.get(current_stage, [])
        if not knockout_matches:
            await update.message.reply_text("❌ Knockout matches for this stage are not yet drawn\.", parse_mode=ParseMode.MARKDOWN_V2)
            print(f"DEBUG: No knockout matches for stage {current_stage}.")
            return

        # Header for knockout stage matches
        stage_title_escaped_header = escape_markdown_v2(current_stage.replace('_', ' ').title())
        player_team_escaped_header = escape_markdown_v2(player_info.get('team', ''))
        reply_text += f"📅 Your Knockout Match \- {player_team_escaped_header} \({stage_title_escaped_header}\)\n\n"

        for match_index, match in enumerate(knockout_matches):
            if not isinstance(match, list) or len(match) < 5:# Assuming knockout matches are 4 elements
                print(f"WARNING: Malformed knockout match data (too short) in Firebase for stage {current_stage}, Match index {match_index}: {match}")
                continue

            print(f"DEBUG: Processing knockout match {match_index}: {match}")
            if user_id == match[0] or user_id == match[1]:
                opponent_id = match[1] if match[0] == user_id else match[0]
                opponent_info = players.get(opponent_id)
                print(f"DEBUG: Opponent info for {opponent_id}: {opponent_info}")

                if opponent_info:
                    # Escape all dynamic text that goes into the reply
                    player_team_escaped = escape_markdown_v2(player_info.get('team', ''))
                    opponent_team_escaped = escape_markdown_v2(opponent_info.get('team', ''))
                    opponent_username_escaped = escape_markdown_v2(opponent_info.get('username', ''))
                    stage_title_escaped = escape_markdown_v2(current_stage.replace('_', ' ').title())

                    if match[2] is not None and match[3] is not None:
                        # If match is finished, display as scoreboard
                        reply_text += (
                            f"🏆 Match Result \(*{stage_title_escaped}*\):\n"
                            f"*{player_team_escaped} {match[2]} \- {match[3]} {opponent_team_escaped}*\n"
                            f"🎮 Opponent: @{opponent_username_escaped}\n\n"
                        )
                    else:
                        # If match is pending
                        reply_text += (
                            f"📅 Your Match \(*{stage_title_escaped}*\):\n"
                            f"{player_team_escaped} vs {opponent_team_escaped} \(Pending\)\n"
                            f"🎮 Opponent: @{opponent_username_escaped}\n\n"
                        )
                    found_fixture = True
                    print(f"DEBUG: Fixture found and added to reply for {user_id}.")
                    break # Stop after finding the user's match
                else:
                    print(f"DEBUG: Opponent {opponent_id} not found in 'players' data for knockout match.")
                    # Handle case where opponent info is missing, still display partial info
                    player_team_escaped = escape_markdown_v2(player_info.get('team', ''))
                    stage_title_escaped = escape_markdown_v2(current_stage.replace('_', ' ').title())
                    reply_text += (
                        f"📅 Your Match \(*{stage_title_escaped}*\):\n"
                        f"{player_team_escaped} vs Unknown Opponent \(Pending\)\n"
                        f"🎮 Opponent: @unknown\n\n" # Fallback if username is missing
                    )
                    found_fixture = True # We still found *a* fixture, even if opponent is missing.
                    break
            else:
                print(f"DEBUG: User {user_id} not in knockout match {match_index}'s player IDs ({match[0]}, {match[1]}).")

        # If after checking all knockout matches, no fixture was found for the user
        if not found_fixture:
            reply_text = "❌ No upcoming match found for you or your matches are already completed for this stage\."
            print(f"DEBUG: No fixture found for {user_id} in {current_stage}. 'found_fixture' remained False.")

    # Final send of the message
    if reply_text: # Ensure reply_text is not empty before sending
        try:
            await update.message.reply_text(reply_text, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            print(f"ERROR: Could not send reply_text due to markdown parsing error: {e}")
            print(f"Problematic reply_text:\n{reply_text}")
            # Fallback to plain text if MarkdownV2 fails
            await update.message.reply_text("An error occurred while formatting the message\. Please contact admin\. Here's the raw info:\n" + escape_markdown_v2(reply_text), parse_mode=ParseMode.MARKDOWN_V2)
    else:
        # This case should ideally not be reached if the logic above is sound
        # but as a safeguard, if reply_text is somehow still empty
        await update.message.reply_text("No fixtures to display at this moment\.", parse_mode=ParseMode.MARKDOWN_V2)


async def group_standings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Load tournament state to check the current stage
    tournament_state = load_state("tournament_state")
    current_stage = tournament_state.get("stage", "registration")

    # --- NEW LOGIC TO DISABLE DURING KNOCKOUT STAGES ---
    if current_stage in ["round_of_16", "quarter_finals", "semi_finals", "final"]:
        await update.message.reply_text(
            "🚫 The tournament has moved to the knockout stage\\. Group standings are no longer available\\.\n"
            "Use /fixtures to see *your* upcoming match, or */showknockout* to see all matches for the current stage\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        print(f"DEBUG: Group standings command blocked because tournament is in {current_stage}.")
        return
    # --- END OF NEW LOGIC ---

    players = load_state("players")
    groups_data = load_state("groups") # This holds player_ids grouped by group_name

    if not groups_data:
        await update.message.reply_text("❌ Groups have not been formed yet.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    all_standings = ""
    for group_name in sorted(groups_data.keys()):
        player_ids = groups_data[group_name]
        standings = []
        for p_id in player_ids:
            player_info = players.get(p_id)
            if player_info:
                stats = player_info.get("stats", {})
                # Ensure team name is safe for MarkdownV2 before using
                team_name_escaped = escape_markdown_v2(player_info.get('team', 'N/A'))
                standings.append({
                    "team_escaped": team_name_escaped, # Use the escaped version
                    "points": stats.get("points", 0),
                    "gd": stats.get("gd", 0),
                    "gf": stats.get("gf", 0),
                    "wins": stats.get("wins", 0),
                    "draws": stats.get("draws", 0),
                    "losses": stats.get("losses", 0),
                    "played": stats.get("wins", 0) + stats.get("draws", 0) + stats.get("losses", 0) # Calculate played matches
                })

        # Sort by points, then GD, then GF (all descending)
        standings.sort(key=lambda x: (x['points'], x['gd'], x['gf']), reverse=True)

        # Using f-strings for formatting without monospace backticks
        group_text = f"📊 *{escape_markdown_v2(group_name.upper())} Standings:*\n"
        # Header is now simpler or removed, as strict columns are hard without monospace
        # If you still want a header, it's best as general text, not attempting strict alignment.
        group_text += "`Team` | `P` | `W` | `D` | `L` | `GD` | `GF` | `Pts`\n" # A simplified, non-aligned header for clarity
        group_text += "----------------------------------------------\n" # Separator

        for team_stat in standings:
            # Format each line concisely
            # Note: without monospace, strict alignment will not work.
            # We'll rely on the simplicity of the format.
            group_text += (
                f"{team_stat['team_escaped']} "
                f"\\({team_stat['wins']}\\-{team_stat['draws']}\\-{team_stat['losses']}\\) "
                f"Pts:*{team_stat['points']}* GD:*{team_stat['gd']}* GF:*{team_stat['gf']}*\n"
                # Using escaped parentheses and dashes for consistency with MarkdownV2
            )
        group_text += "\n" # Add a newline between groups
        all_standings += group_text

    if all_standings:
        await update.message.reply_text(all_standings, parse_mode=ParseMode.MARKDOWN_V2)
    else:
        await update.message.reply_text("❌ No standings available yet.", parse_mode=ParseMode.MARKDOWN_V2)
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
# Assuming current_admin_matches = {} is defined globally at the top of your script

async def addscore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global current_admin_matches # Declare global scope for modification
    ADMIN_ID="7366894756"
    if str(update.effective_user.id) != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized\\.", parse_mode=ParseMode.MARKDOWN_V2) 
        return

    fixtures_data = load_state("fixtures")
    players_data = load_state("players")
    tournament_state = load_state("tournament_state")
    current_stage = tournament_state.get("stage")
    current_group_round = tournament_state.get("group_match_round", 0) 

    if not fixtures_data or not current_stage:
        await update.message.reply_text("❌ No matches currently scheduled for any stage\\.", parse_mode=ParseMode.MARKDOWN_V2) 
        return

    # Beautified header
    reply = f"📅 *Upcoming Matches for {escape_markdown_v2(current_stage.replace('_', ' ').title())}:*\n\n"
    
    current_admin_matches.clear() 
    idx = 1 

    if current_stage == "group_stage":
        for group_name, matches in fixtures_data.get("group_stage", {}).items():
            for match in matches:
                if not isinstance(match, list) or len(match) < 5 or match[4] != current_group_round:
                    continue 

                p1_id, p2_id, score1, score2, round_num = match 

                if score1 is None: 
                    p1 = players_data.get(p1_id)
                    p2 = players_data.get(p2_id)
                    if p1 and p2:
                        current_admin_matches[f"match{idx}"] = {
                            "type": "group",
                            "group": group_name,
                            "p1_id": p1_id,
                            "p2_id": p2_id,
                            "round_num": round_num 
                        }
                        # Added emojis and consistent MarkdownV2 escaping
                        reply += (
                            f"✨ /{escape_markdown_v2(f'match{idx}')} → " 
                            f"*{escape_markdown_v2(p1.get('team', 'Unknown Player'))}* vs *{escape_markdown_v2(p2.get('team', 'Unknown Player'))}* "
                            f"\\(Group {escape_markdown_v2(group_name)} \\- Round {round_num + 1}\\)\n" 
                        )
                        idx += 1
    elif current_stage in ["round_of_16", "quarter_finals", "semi_finals", "final"]:
        for match in fixtures_data.get(current_stage, []):
            if not isinstance(match, list) or len(match) < 4:
                print(f"WARNING: Skipping malformed knockout match: {match}")
                continue 

            p1_id, p2_id, score1, score2 = match[:4] 
            if score1 is None: 
                p1 = players_data.get(p1_id)
                p2 = players_data.get(p2_id)
                if p1 and p2:
                    current_admin_matches[f"match{idx}"] = {"type": "knockout", "stage": current_stage, "p1_id": p1_id, "p2_id": p2_id}
                    # Added emojis and consistent MarkdownV2 escaping
                    reply += (
                        f"⚔️ /{escape_markdown_v2(f'match{idx}')} → " 
                        f"*{escape_markdown_v2(p1.get('team', 'Unknown Player'))}* vs *{escape_markdown_v2(p2.get('team', 'Unknown Player'))}* "
                        f"\\({escape_markdown_v2(current_stage.replace('_', ' ').title())}\\)\n"
                    )
                    idx += 1
    
    # --- Provide feedback if no matches found (Beautified) ---
    if idx == 1: 
        reply = f"👍 *All matches for the current {escape_markdown_v2(current_stage.replace('_', ' ').title())} are complete\!* ✅"
        
        if current_stage == "group_stage":
            reply += f"\nAdmin can now use /{escape_markdown_v2('advance_group_round')} to proceed\\." 
        elif current_stage == "group_stage_completed":
            reply += f"\nGroup stage is finished\\. Admin needs to draw knockout stages\\." 

    # --- Add score reporting instruction (Beautified slightly) ---
    reply += f"\n\nTo add score: /{escape_markdown_v2('match1')} 2\\-1" 

    await update.message.reply_text(reply, parse_mode=ParseMode.MARKDOWN_V2)

async def handle_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ADMIN_ID="7366894756"
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
        f"✅ *Group Match Result:*\n" 
        f"*{escape_markdown_v2(p1_team_name)} {score1} \- {score2} {escape_markdown_v2(p2_team_name)}*\n\n"
        f"_➡️ Check /standings for updated standings\! 📊_",
        parse_mode=ParseMode.MARKDOWN_V2
    )

    # The logic to check if all matches are completed and advance to knockout
    # is now handled by the /advance_group_round command, NOT here.

async def advance_group_round(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ADMIN_ID="7366894756"
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Only the admin can advance tournament rounds.")
        return

    tournament_state = load_state("tournament_state")
    fixtures_data = load_state("fixtures")
    players = load_state("players") # Needed to show team names

    current_stage = tournament_state.get("stage")
    current_group_round = tournament_state.get("group_match_round", 0)

    if current_stage != "group_stage":
        await update.message.reply_text(f"❌ Tournament is not in the group stage. Current stage: {current_stage}. Cannot advance group rounds.")
        return

    group_stage_fixtures = fixtures_data.get("group_stage", {})

    # 1. Check if ALL matches for the current round are completed
    pending_matches_in_current_round = []
    # Loop through all groups
    for group_name, matches_in_group in group_stage_fixtures.items():
        # Loop through matches within each group
        for match_data in matches_in_group:
            # Ensure it's a valid match list with 5 elements (player1_id, player2_id, score1, score2, round_number)
            # And ensure it belongs to the current_group_round being checked
            if isinstance(match_data, list) and len(match_data) >= 5 and match_data[4] == current_group_round:
                # Check if scores are missing (None)
                if match_data[2] is None or match_data[3] is None:
                    player1_id, player2_id, _, _, _ = match_data
                    
                    # Get player teams for display, using escape_markdown_v2
                    player1_team = players.get(player1_id, {}).get('team', f"Player {player1_id}")
                    player2_team = players.get(player2_id, {}).get('team', f"Player {player2_id}")
                    
                    pending_matches_in_current_round.append(
                        f"- {escape_markdown_v2(player1_team)} vs {escape_markdown_v2(player2_team)} (Group {escape_markdown_v2(group_name)})"
                    )

    if pending_matches_in_current_round:
        # If there are pending matches, inform the admin
        reply_text = f"❌ Cannot advance to the next round. The following matches in Round {current_group_round + 1} are still pending:\n"
        reply_text += "\n".join(pending_matches_in_current_round)
        await update.message.reply_text(reply_text, parse_mode='Markdown')
        return

    # 2. If all current round matches are complete, check if there are more rounds or if group stage is finished
    max_group_rounds = 3 # This should match the total number of rounds you generate (0-indexed: 0, 1, 2 for 3 rounds)

    if current_group_round < max_group_rounds - 1: # -1 because rounds are 0-indexed (e.g., if max=3, rounds are 0,1,2. We advance if current is 0 or 1)
        tournament_state["group_match_round"] += 1 # Increment to the next round
        save_state("tournament_state", tournament_state)
        new_round_number = tournament_state["group_match_round"] + 1 # For user-friendly display (1-indexed)

        await update.message.reply_text(
            f"✅ All matches for Round {current_group_round + 1} are completed! Advancing to Round {new_round_number}.",
            parse_mode='Markdown' # Ensure Markdown is applied
        )
        await context.bot.send_message(
            GROUP_ID,
            f"📣 Group stage has advanced! Round {new_round_number} matches are now active. Use /fixtures to see your new match.",
            parse_mode='Markdown'
        )
    else:
        # All group rounds are finished (current_group_round is now max_group_rounds - 1, meaning the last round's matches are done)
        tournament_state["stage"] = "group_stage_completed" # Update stage to indicate group stage is over
        save_state("tournament_state", tournament_state)

        await update.message.reply_text(
            "🎉 All group stage matches are completed! The group stage has ended. Calculating standings and preparing for Knockouts...",
            parse_mode='Markdown'
        )
        await context.bot.send_message(
            GROUP_ID,
            "🎉 The Group Stage has concluded! Calculating final standings and preparing for Knockouts (to be drawn by admin). Use /standings to see final group rankings.",
            parse_mode='Markdown'
        )
        
        # This is where your 'advance_to_knockout' function gets called,
        # after ALL group rounds are complete and confirmed by the admin via /advance_group_round.
        await advance_to_knockout(context)
async def advance_to_knockout(context: ContextTypes.DEFAULT_TYPE):
    print("DEBUG: Entering advance_to_knockout function.")
    tournament_state = load_state("tournament_state")
    players = load_state("players")
    fixtures_data = load_state("fixtures")
    # ADMIN_ID="7366894756" # Make sure ADMIN_ID is defined elsewhere, e.g., globally
    ADMIN_ID = "7366894756" # Temporary for demonstration, define this properly!
    GROUP_ID = -100 # Placeholder, define this properly!


    # Ensure tournament is in 'group_stage_completed' before proceeding
    if tournament_state.get("stage") != "group_stage_completed":
        print(f"DEBUG: advance_to_knockout called, but tournament state is not 'group_stage_completed'. Current stage: {tournament_state.get('stage')}. Aborting.")
        await context.bot.send_message(ADMIN_ID, "❌ Knockout stage cannot be initiated. Group stage not marked as completed.")
        return

    # 1. Calculate final group standings (if not already done by advance_group_round)
    # This recalculates to ensure the most up-to-date standings for seeding
    # This logic should mirror parts of your /standings command's calculation for group stage
    for group_name, group_matches in fixtures_data.get("group_stage", {}).items():
        group_players = {} # player_id: player_stats
        for match in group_matches:
            # Ensure match data is complete before processing (player1, player2, score1, score2, round)
            if len(match) == 5 and all(x is not None for x in [match[0], match[1], match[2], match[3]]):
                p1_id, p2_id, score1, score2, _ = match # _ here is the round number, which is fine
                
                # Initialize player stats if not present
                if p1_id not in group_players:
                    group_players[p1_id] = {'wins': 0, 'draws': 0, 'losses': 0, 'gf': 0, 'ga': 0, 'gd': 0, 'points': 0}
                if p2_id not in group_players:
                    group_players[p2_id] = {'wins': 0, 'draws': 0, 'losses': 0, 'gf': 0, 'ga': 0, 'gd': 0, 'points': 0}

                # Update stats for player 1
                group_players[p1_id]['gf'] += score1
                group_players[p1_id]['ga'] += score2
                group_players[p1_id]['gd'] += (score1 - score2)

                # Update stats for player 2
                group_players[p2_id]['gf'] += score2
                group_players[p2_id]['ga'] += score1
                group_players[p2_id]['gd'] += (score2 - score1)

                if score1 > score2:
                    group_players[p1_id]['wins'] += 1
                    group_players[p1_id]['points'] += 3
                    group_players[p2_id]['losses'] += 1
                elif score1 < score2:
                    group_players[p2_id]['wins'] += 1
                    group_players[p2_id]['points'] += 3
                    group_players[p1_id]['losses'] += 1
                else:
                    group_players[p1_id]['draws'] += 1
                    group_players[p1_id]['points'] += 1
                    group_players[p2_id]['draws'] += 1
                    group_players[p2_id]['points'] += 1
            else:
                print(f"WARNING: Skipping incomplete match in group stage standings calculation: {match}")

        # Apply updated stats back to the main players dictionary
        for p_id, stats in group_players.items():
            if p_id in players:
                players[p_id]['stats'] = stats
            else:
                print(f"WARNING: Player {p_id} not found in 'players' dictionary during standings update.")
    
    save_state("players", players) # Save updated player stats

    # 2. Collect all players eligible for knockout based on their group standings
    # Flatten all group standings into a single list of (player_id, player_info) tuples
    all_qualified_players = []
    for player_id, p_info in players.items():
        if p_info.get('group'): # Ensure player was in a group
            # Ensure player has stats for sorting
            if 'stats' in p_info and p_info['stats']['points'] is not None:
                all_qualified_players.append((player_id, p_info))
            else:
                print(f"WARNING: Player {player_id} ({p_info.get('name')}) has no stats. Skipping for knockout qualification.")

    # Sort players by points, then GD, then GF (standard football tie-breakers)
    sorted_qualified_players = sorted(
        all_qualified_players,
        key=lambda x: (x[1]['stats']['points'], x[1]['stats']['gd'], x[1]['stats']['gf']),
        reverse=True # Highest points/GD/GF first
    )

    # Extract just the player IDs for pairing
    qualified_player_ids_for_pairing = [p_id for p_id, _ in sorted_qualified_players]

    print(f"DEBUG: Qualified players (sorted): {[(players[p_id]['team'], players[p_id]['stats']['points']) for p_id in qualified_player_ids_for_pairing]}")


    # 3. Create knockout bracket (e.g., Round of 16)
    # This logic assumes you have enough players for a full 16-team bracket (or 8-team, etc.)
    # Adjust `num_knockout_players_needed` based on your bracket size
    num_knockout_players_needed = 16 # For Round of 16
    
    if len(qualified_player_ids_for_pairing) < num_knockout_players_needed:
        await context.bot.send_message(
            ADMIN_ID, 
            f"❌ Not enough qualified players ({len(qualified_player_ids_for_pairing)}) to form a full Round of 16 bracket ({num_knockout_players_needed} needed). Cannot proceed to knockouts."
        )
        print(f"ERROR: Not enough players for knockout stage: {len(qualified_player_ids_for_pairing)} out of {num_knockout_players_needed} needed.")
        tournament_state["stage"] = "group_stage_incomplete" # Mark it as such for admin
        save_state("tournament_state", tournament_state)
        return

    # Take only the top N players for the knockout stage
    top_n_players = qualified_player_ids_for_pairing[:num_knockout_players_needed]

    knockout_fixtures_r16 = []
    
    # Manual pairing based on seeding (adjust if your seeding logic is different)
    if num_knockout_players_needed == 16:
        seeds = top_n_players # top_n_players is already sorted by rank (seed)
        # ADDED 0 AS THE FIFTH ELEMENT FOR KNOCKOUT MATCHES
        knockout_fixtures_r16.append([seeds[0], seeds[15], None, None, 0]) # 1 vs 16
        knockout_fixtures_r16.append([seeds[7], seeds[8], None, None, 0])  # 8 vs 9
        knockout_fixtures_r16.append([seeds[4], seeds[11], None, None, 0]) # 5 vs 12
        knockout_fixtures_r16.append([seeds[3], seeds[12], None, None, 0]) # 4 vs 13
        knockout_fixtures_r16.append([seeds[2], seeds[13], None, None, 0]) # 3 vs 14
        knockout_fixtures_r16.append([seeds[5], seeds[10], None, None, 0]) # 6 vs 11
        knockout_fixtures_r16.append([seeds[6], seeds[9], None, None, 0])  # 7 vs 10
        knockout_fixtures_r16.append([seeds[1], seeds[14], None, None, 0]) # 2 vs 15
    elif num_knockout_players_needed == 8: # Example for Quarter Finals directly
        seeds = top_n_players
        # ADDED 0 AS THE FIFTH ELEMENT FOR KNOCKOUT MATCHES
        knockout_fixtures_r16.append([seeds[0], seeds[7], None, None, 0]) # 1 vs 8
        knockout_fixtures_r16.append([seeds[3], seeds[4], None, None, 0]) # 4 vs 5
        knockout_fixtures_r16.append([seeds[2], seeds[5], None, None, 0]) # 3 vs 6
        knockout_fixtures_r16.append([seeds[1], seeds[6], None, None, 0]) # 2 vs 7
    # Add more `elif` blocks here for other bracket sizes if needed

    fixtures_data["round_of_16"] = knockout_fixtures_r16 # Store for Round of 16
    save_state("fixtures", fixtures_data)
    print(f"DEBUG: Knockout fixtures (Round of 16) saved: {json.dumps(knockout_fixtures_r16, indent=2)}")

    # 4. Update tournament state to reflect knockout stage
    tournament_state["stage"] = "round_of_16" # Mark tournament as being in Round of 16
    # Also reset `group_match_round` as it's no longer relevant for knockouts
    if "group_match_round" in tournament_state:
        del tournament_state["group_match_round"] 
    save_state("tournament_state", tournament_state)
    print(f"DEBUG: Tournament state updated to: {tournament_state['stage']}")

    # 5. Send notifications
    await context.bot.send_message(ADMIN_ID, "🎉 Group Stage is over! The Knockout Stage (Round of 16) has begun!\nCheck /fixtures for the new matchups!")
    await context.bot.send_message(GROUP_ID, "🎉 The Group Stage has concluded! The Knockout Stage (Round of 16) has begun!\nCheck /fixtures for your new matchup!")
    print("DEBUG: Notifications sent for knockout stage start.")

async def notify_knockout_matches(context: ContextTypes.DEFAULT_TYPE, stage: str):
    print(f"DEBUG: notify_knockout_matches called for stage: {stage}")
    fixtures_data = load_state("fixtures")
    players_data = load_state("players")

    matches = fixtures_data.get(stage, [])
    if not matches:
        print(f"DEBUG: No matches found for stage {stage}. Not sending notification.")
        return

    # Escape the stage title itself for the header
    stage_title_escaped = escape_markdown_v2(stage.replace('_', ' ').title())
    message = f"📢 *{stage_title_escaped} Matches:*\n\n"

    for match in matches:
        # Ensure match has enough elements (player1_id, player2_id, score1, score2)
        # We now know knockout matches have 4 elements (including score placeholders)
        if not isinstance(match, list) or len(match) < 4:
            print(f"WARNING: Skipping malformed match in notify_knockout_matches: {match}")
            continue

        p1_id, p2_id, score1, score2 = match
        p1_info = players_data.get(p1_id)
        p2_info = players_data.get(p2_id)
        
        if p1_info and p2_info:
            # Escape all dynamic parts of the string that might contain Markdown special characters
            p1_team_escaped = escape_markdown_v2(p1_info.get('team', f"Player {p1_id}"))
            p1_username_escaped = escape_markdown_v2(p1_info.get('username', f"user_{p1_id}"))
            p2_team_escaped = escape_markdown_v2(p2_info.get('team', f"Player {p2_id}"))
            p2_username_escaped = escape_markdown_v2(p2_info.get('username', f"user_{p2_id}"))

            score_display = ""
            if score1 is not None and score2 is not None:
                score_display = f" ({score1}-{score2})" # Display scores if available
            
            message += (
                f"{p1_team_escaped} (@{p1_username_escaped}) "
                f"vs {p2_team_escaped} (@{p2_username_escaped}){score_display}\n"
            )
        else:
            print(f"WARNING: Could not find player info for match {match} in notify_knockout_matches.")

    message += "\n_Good luck to all participants!_"
    
    await context.bot.send_message(GROUP_ID, message, parse_mode=ParseMode.MARKDOWN_V2) # Use ParseMode.MARKDOWN_V2 for clarity
    print("DEBUG: Knockout matches notification sent successfully.")



async def handle_knockout_score(update: Update, context: ContextTypes.DEFAULT_TYPE, stage: str, p1_id: str, p2_id: str, score1: int, score2: int):
    print(f"DEBUG: handle_knockout_score called for stage {stage} with {p1_id}-{p2_id} score {score1}-{score2}")
    
    fixtures_data = load_state("fixtures")
    players_data = load_state("players")
    tournament_state = load_state("tournament_state")

    # --- Input Validation and Pre-processing ---
    try:
        score1 = int(score1)
        score2 = int(score2)
    except ValueError:
        await update.message.reply_text("❌ Scores must be numbers.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    if score1 == score2:
        await update.message.reply_text("❌ Knockout matches cannot be a draw\\. Please enter a decisive score\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    winner_id = p1_id if score1 > score2 else p2_id
    loser_id = p2_id if score1 > score2 else p1_id

    winner_info = players_data.get(winner_id)
    loser_info = players_data.get(loser_id)

    if not winner_info or not loser_info:
        await update.message.reply_text("❌ Error: Could not find player information for one or both participants\\.", parse_mode=ParseMode.MARKDOWN_V2)
        print(f"ERROR: Missing player info for winner_id={winner_id} or loser_id={loser_id}.")
        return

    # Escape player/team/username info and stage title for Markdown messages
    winner_team_escaped = escape_markdown_v2(winner_info.get('team', 'Unknown Team'))
    winner_username_escaped = escape_markdown_v2(winner_info.get('username', 'unknown_user'))
    loser_team_escaped = escape_markdown_v2(loser_info.get('team', 'Unknown Team'))
    loser_username_escaped = escape_markdown_v2(loser_info.get('username', 'unknown_user'))
    stage_title_escaped = escape_markdown_v2(stage.replace('_', ' ').title())

    # --- Find and Update the Match in Fixtures ---
    current_matches = fixtures_data.get(stage, [])
    match_found_and_updated = False
    for i, match in enumerate(current_matches):
        if not isinstance(match, list) or len(match) < 2:
            print(f"WARNING: Skipping malformed match in current_matches: {match}")
            continue

        if (match[0] == p1_id and match[1] == p2_id):
            current_matches[i] = [p1_id, p2_id, score1, score2]
            match_found_and_updated = True
            break
        elif (match[0] == p2_id and match[1] == p1_id):
            current_matches[i] = [p2_id, p1_id, score2, score1] 
            match_found_and_updated = True
            break

    if not match_found_and_updated:
        await update.message.reply_text("❌ Error: Knockout match not found or already processed in fixtures for this stage\\.", parse_mode=ParseMode.MARKDOWN_V2)
        print(f"ERROR: Knockout match {p1_id}-{p2_id} not found/updated in stage {stage}.")
        return

    fixtures_data[stage] = current_matches
    save_state("fixtures", fixtures_data)
    print(f"DEBUG: Fixtures data saved for stage {stage} after score update.")

    # --- Send Confirmation Messages (Beautified) ---
    # Confirmation for the admin
    await update.message.reply_text(
        f"🎉 *Match Result Recorded!* Score {score1}-{score2} for {winner_team_escaped} vs {loser_team_escaped}\\. "
        f"*{winner_team_escaped}* advances\\! @{winner_username_escaped}",
        parse_mode=ParseMode.MARKDOWN_V2
    )
    # Announcement to the main group
    await context.bot.send_message(
        GROUP_ID,
        f"🏆 *KNOCKOUT BATTLE!* \\- *{stage_title_escaped}*\n" # Escaped hyphen
        f"*{winner_team_escaped}* {score1} \\- {score2} *{loser_team_escaped}*\n" # Escaped hyphen
        f"🌟 *{winner_team_escaped}* advances to the next round\\! @{winner_username_escaped}", # Escaped exclamation mark
        parse_mode=ParseMode.MARKDOWN_V2
    )
    print(f"DEBUG: Score notification sent for {winner_team_escaped} vs {loser_team_escaped}.")

    # --- Check for Stage Completion and Advance ---
    all_matches_completed = True
    for match in current_matches:
        if match[2] is None or match[3] is None:
            all_matches_completed = False
            break
    
    print(f"DEBUG: All matches in {stage} completed: {all_matches_completed}")

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
            # Tournament is over! (Beautified)
            final_winner_info = players_data.get(winner_id) # Get winner info again, it's the last winner
            final_winner_team_escaped = escape_markdown_v2(final_winner_info.get('team', 'Unknown Team'))
            final_winner_username_escaped = escape_markdown_v2(final_winner_info.get('username', 'unknown_user'))

            await context.bot.send_message(
                GROUP_ID, 
                f"👑 *A NEW CHAMPION IS CROWNED!* 👑\n"
                f"🎉 The tournament has concluded and the winner is *{final_winner_team_escaped}* (@{final_winner_username_escaped})\\!\n"
                f"Congratulations to the champion and thank you to all participants\\! 🙏", # Escaped exclamation mark
                parse_mode=ParseMode.MARKDOWN_V2
            )
            tournament_state["stage"] = "completed"
            save_state("tournament_state", tournament_state)
            print("DEBUG: Tournament completed!")
            return

        winners_of_current_stage_ordered = []
        for match in current_matches:
            if match[2] is not None and match[3] is not None:
                winner = match[0] if match[2] > match[3] else match[1]
                winners_of_current_stage_ordered.append(winner)
            else:
                print(f"WARNING: Found incomplete match while collecting winners for next stage: {match}")

        next_stage_fixtures = []
        for i in range(0, len(winners_of_current_stage_ordered), 2):
            if i + 1 < len(winners_of_current_stage_ordered):
                next_stage_fixtures.append([winners_of_current_stage_ordered[i], winners_of_current_stage_ordered[i+1], None, None])
            else:
                print(f"WARNING: Odd number of winners ({len(winners_of_current_stage_ordered)}) for {next_stage}. This indicates an issue in bracket generation or reporting.")

        fixtures_data[next_stage] = next_stage_fixtures
        tournament_state["stage"] = next_stage
        save_state("fixtures", fixtures_data)
        save_state("tournament_state", tournament_state)
        print(f"DEBUG: Advanced to {next_stage}. New fixtures: {json.dumps(next_stage_fixtures, indent=2)}")

        # Notify the group about advancing to the next stage and new matches (Beautified)
        await context.bot.send_message(
            GROUP_ID, 
            f"🌟 *ALL MATCHES CONCLUDED!* \\- *{stage_title_escaped}*\n" # Escaped hyphen
            f"🥳 Advancing to {escape_markdown_v2(next_stage.replace('_', ' ').title())}\\! Get ready for the next round of battles\\! 💪", # Escaped exclamation mark
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await notify_knockout_matches(context, next_stage)
        print(f"DEBUG: Notifications sent for {next_stage} start.")

async def mygroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    print(f"DEBUG: MyGroup command received from user_id: {user_id}")

    players = load_state("players")
    tournament_state = load_state("tournament_state")
    fixtures_data = load_state("fixtures") # Needed for group stage matches to calculate standings
    
    current_stage = tournament_state.get("stage", "registration")

    if user_id not in players:
        await update.message.reply_text("❌ You are not registered for the tournament\\. Use /register\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    player_info = players[user_id]
    player_group = player_info.get("group")

    if not player_group:
        await update.message.reply_text("❌ You are not assigned to any group yet\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    reply_text = ""
    reply_markup = None # Initialize reply_markup to None

    if current_stage == "group_stage":
        # --- Display Group Standings for user's group (simplified) ---
        
        group_matches = fixtures_data.get("group_stage", {}).get(player_group, [])
        
        # Calculate standings for this specific group
        group_players_stats = {} 
        # Initialize players in the group even if they have no matches, so they appear
        # Also, fetch their initial stats (if any) and username
        for p_id_in_group in load_state("groups").get(player_group, []):
            if p_id_in_group in players:
                 p_details = players[p_id_in_group]
                 group_players_stats[p_id_in_group] = {
                     'team': p_details.get('team', 'N/A'),
                     'username': p_details.get('username', 'N/A'),
                     'points': p_details.get("stats", {}).get("points", 0),
                     'gd': p_details.get("stats", {}).get("gd", 0), # Still need GD/GF for sorting
                     'gf': p_details.get("stats", {}).get("gf", 0),
                     'wins': p_details.get("stats", {}).get("wins", 0),
                     'draws': p_details.get("stats", {}).get("draws", 0),
                     'losses': p_details.get("stats", {}).get("losses", 0)
                 }

        # Update stats based on completed matches in this group
        for match in group_matches:
            if len(match) == 5 and all(x is not None for x in [match[0], match[1], match[2], match[3]]):
                p1_id, p2_id, score1, score2, _ = match

                # Ensure players are initialized in temp stats dict
                for p_id_in_match in [p1_id, p2_id]:
                    if p_id_in_match not in group_players_stats:
                        p_details = players.get(p_id_in_match, {})
                        group_players_stats[p_id_in_match] = {
                            'team': p_details.get('team', 'N/A'),
                            'username': p_details.get('username', 'N/A'),
                            'points': p_details.get("stats", {}).get("points", 0),
                            'gd': p_details.get("stats", {}).get("gd", 0),
                            'gf': p_details.get("stats", {}).get("gf", 0),
                            'wins': p_details.get("stats", {}).get("wins", 0),
                            'draws': p_details.get("stats", {}).get("draws", 0),
                            'losses': p_details.get("stats", {}).get("losses", 0)
                        }

                # Update stats for player 1
                group_players_stats[p1_id]['gf'] += score1
                group_players_stats[p1_id]['ga'] += score2
                group_players_stats[p1_id]['gd'] += (score1 - score2)

                # Update stats for player 2
                group_players_stats[p2_id]['gf'] += score2
                group_players_stats[p2_id]['ga'] += score1
                group_players_stats[p2_id]['gd'] += (score2 - score1)

                if score1 > score2:
                    group_players_stats[p1_id]['wins'] += 1
                    group_players_stats[p1_id]['points'] += 3
                    group_players_stats[p2_id]['losses'] += 1
                elif score1 < score2:
                    group_players_stats[p2_id]['wins'] += 1
                    group_players_stats[p2_id]['points'] += 3
                    group_players_stats[p1_id]['losses'] += 1
                else:
                    group_players_stats[p1_id]['draws'] += 1
                    group_players_stats[p1_id]['points'] += 1
                    group_players_stats[p2_id]['draws'] += 1
                    group_players_stats[p2_id]['points'] += 1

        standings_list = []
        for p_id, stats in group_players_stats.items():
            standings_list.append({
                "team": stats['team'],
                "username": stats['username'],
                "points": stats['points'],
                "gd": stats['gd'],
                "gf": stats['gf'],
            })

        standings_list.sort(key=lambda x: (x['points'], x['gd'], x['gf']), reverse=True)

        reply_text += f"*📊 Your Group \\({escape_markdown_v2(player_group.upper())}\\) Standings:*\n\n"
        # Simplified header
        reply_text += "`Rank | Team Name   | Username  | Pts`\n"
        reply_text += "`-----|-------------|-----------|----`\n"

        for rank, team_stat in enumerate(standings_list):
            team_display = (team_stat['team'] + "            ")[:12] # Pad and truncate
            username_display = (team_stat['username'] + "          ")[:10] # Pad and truncate
            
            escaped_team_display = escape_markdown_v2(team_display)
            escaped_username_display = escape_markdown_v2(username_display)

            reply_text += (
                f"`{rank + 1:<4} | {escaped_team_display:<12} | {escaped_username_display:<10} | {team_stat['points']:<3}`\n"
            )
        
        reply_text += "\n"
        
        keyboard = [[InlineKeyboardButton("View All Your Fixtures", callback_data="show_my_fixtures")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

    elif current_stage in ["round_of_16", "quarter_finals", "semi_finals", "final"]:
        # --- Display Qualified Teams from user's group (with username and points) ---
        
        reply_text += f"*🏆 Group {escape_markdown_v2(player_group.upper())} Qualifiers:*\n\n"
        
        players_in_my_group = [
            (p_id, p_info) for p_id, p_info in players.items() 
            if p_info.get('group') == player_group and 'stats' in p_info
        ]

        sorted_my_group_players = sorted(
            players_in_my_group,
            key=lambda x: (x[1]['stats']['points'], x[1]['stats']['gd'], x[1]['stats']['gf']),
            reverse=True
        )

        if len(sorted_my_group_players) >= 2:
            top_1 = sorted_my_group_players[0]
            top_2 = sorted_my_group_players[1]
            
            # Extract and escape team, username, and points for qualified teams
            escaped_team1 = escape_markdown_v2(top_1[1].get('team', 'N/A'))
            escaped_username1 = escape_markdown_v2(top_1[1].get('username', 'N/A'))
            points1 = top_1[1].get('stats', {}).get('points', 0)

            escaped_team2 = escape_markdown_v2(top_2[1].get('team', 'N/A'))
            escaped_username2 = escape_markdown_v2(top_2[1].get('username', 'N/A'))
            points2 = top_2[1].get('stats', {}).get('points', 0)
            
            reply_text += f"1\\. *{escaped_team1}* \\(@{escaped_username1}\\) \\({points1} Pts\\) \\(Qualified✅\\)\n"
            reply_text += f"2\\. *{escaped_team2}* \\(@{escaped_username2}\\) \\({points2} Pts\\) \\(Qualified✅\\)\n\n"
            
            if len(sorted_my_group_players) > 2:
                reply_text += "Other teams in your group did not qualify\\.\n\n"
        else:
            reply_text += "Not enough players in your group to determine qualifiers yet\\.\n\n"
            
        keyboard = [[InlineKeyboardButton("View Current Knockout Matches", callback_data="show_all_knockout_matches")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

    else:
        await update.message.reply_text(
            f"Tournament is in '{escape_markdown_v2(current_stage)}' stage\\. Group information is not yet applicable\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    
    try:
        await update.message.reply_text(reply_text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=reply_markup)
    except Exception as e:
        print(f"ERROR: Could not send reply for /mygroup due to markdown parsing error: {e}")
        print(f"Problematic reply_text:\n{reply_text}")
        await update.message.reply_text("An error occurred while formatting your group info\\. Please contact admin\\. Here's the raw info:\n" + escape_markdown_v2(reply_text), parse_mode=ParseMode.MARKDOWN_V2)        
def get_player_team_name(player_id, players_data):
    return players_data.get(player_id, {}).get('team', f'Unknown Player ({player_id})')

async def show_knockout_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fixtures_data = load_state("fixtures")
    players_data = load_state("players")
    tournament_state = load_state("tournament_state")
    current_stage = tournament_state.get("stage")

    knockout_stages_order = ["round_of_16", "quarter_finals", "semi_finals", "final"]

    # Escape current stage title for general messages
    current_stage_title_escaped = escape_markdown_v2(current_stage.replace('_', ' ').title())

    if current_stage not in knockout_stages_order and current_stage != "completed":
        # Beautified message for non-knockout/non-completed stages
        await update.message.reply_text(
            f"ℹ️ *Tournament Stage:* {current_stage_title_escaped} \\(Knockout bracket not active yet\\)\\.\n\n"
            f"Check group details with /showgroups \\! 📊",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    elif current_stage == "completed":
        final_winner_id = None
        final_stage_matches = fixtures_data.get("final", [])
        if final_stage_matches and final_stage_matches[0][2] is not None:
            # Assuming final_stage_matches[0] is the only match and it's played
            match = final_stage_matches[0]
            if match[2] > match[3]:
                final_winner_id = match[0]
            else:
                final_winner_id = match[1]
        
        if final_winner_id:
            winner_team = escape_markdown_v2(get_player_team_name(final_winner_id, players_data))
            # Beautified message for completed tournament with winner
            reply = (
                f"🎉 *Tournament FINISHED!* 🎉\n"
                f"🏆 Your Champion: *{winner_team}*\\!\n\n"
                f"Here's the complete bracket overview:\n\n"
            )
        else:
            # Beautified message for completed tournament without winner result
            reply = (
                f"😔 *Tournament Finished!* 😔\n"
                f"Final match result unavailable\\. Here's the bracket status:\n\n"
            )
    else:
        # Beautified header for active knockout stage
        reply = f"🥊 *Knockout Bracket: {current_stage_title_escaped}* 🥊\n\n"


    # Iterate through all knockout stages to build the full bracket view
    for stage_name in knockout_stages_order:
        stage_title_escaped = escape_markdown_v2(stage_name.replace('_', ' ').title()) # Re-escape for internal stage headers
        matches_in_stage = fixtures_data.get(stage_name, [])

        if not matches_in_stage:
            # Check if this stage is in the future relative to current_stage
            if knockout_stages_order.index(stage_name) > knockout_stages_order.index(current_stage):
                # Beautified message for future stages (not yet drawn)
                reply += f"\-\-\- 🔮 *{stage_title_escaped}:* \\(Matches to be drawn\\) \-\-\-\n\n"
            elif stage_name == "final" and current_stage == "semi_finals":
                # Beautified message for final when semi-final winners are TBD
                 reply += f"\-\-\- 🗓️ *{stage_title_escaped}:* \\(Teams TBD\\) \-\-\-\n\n"
            # else: don't print if it's a past stage that somehow got empty (shouldn't happen with proper flow)
            continue
        
        # Beautified stage header
        reply += f"\-\-\- ✨ *{stage_title_escaped}* ✨ \-\-\-\n"
        
        for match_num, match in enumerate(matches_in_stage, 1):
            if not isinstance(match, list) or len(match) < 4:
                print(f"WARNING: Skipping malformed match in {stage_name}: {match}")
                continue

            p1_id, p2_id, score1, score2 = match[:4]

            p1_team = escape_markdown_v2(get_player_team_name(p1_id, players_data))
            p2_team = escape_markdown_v2(get_player_team_name(p2_id, players_data))

            if score1 is not None and score2 is not None:
                # Beautified completed match line: bold teams, explicit winner arrow
                winner_team = p1_team if score1 > score2 else p2_team
                loser_team = p2_team if score1 > score2 else p1_team
                reply += (
                    f"✅ *{p1_team}* {score1} \\- {score2} *{p2_team}* "
                    f"\\(Winner: *{winner_team}*\\) ➡️\n" # Added right arrow emoji
                )
            else:
                # Beautified pending match line: bold teams, more descriptive status
                reply += f"⏳ *{p1_team}* vs *{p2_team}* \\(Awaiting Result\\) ➡️\n" # Added right arrow emoji
        reply += "\n" # Spacing between stages

    await update.message.reply_text(reply, parse_mode=ParseMode.MARKDOWN_V2)
async def reset_tournament(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ADMIN_ID="7366894756"
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
    application.add_handler(CommandHandler("advance_group_round", advance_group_round))
    application.add_handler(CommandHandler("showknockout", show_knockout_status))
    application.add_handler(CommandHandler("mygroup", mygroup))
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
