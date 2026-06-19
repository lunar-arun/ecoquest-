import os
import random
import datetime
from flask import Flask, request, jsonify, session, render_template
from werkzeug.security import generate_password_hash, check_password_hash
from database import get_db

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "ecoquest-super-secret-key-12345")

# Initialize database connection
db = get_db()

# Action configuration mappings: XP, CO2 savings (kg), and Eco Coin rewards
ACTION_CONFIGS = {
    "Walking instead of driving": {"co2": 1.2, "xp": 50, "coins": 10},
    "Using public transportation": {"co2": 0.8, "xp": 40, "coins": 8},
    "Recycling waste": {"co2": 0.5, "xp": 30, "coins": 6},
    "Using reusable bottles": {"co2": 0.2, "xp": 20, "coins": 4},
    "Planting trees": {"co2": 5.0, "xp": 100, "coins": 20},
    "Eating vegetarian meals": {"co2": 1.5, "xp": 60, "coins": 12},
    "Saving electricity": {"co2": 0.6, "xp": 45, "coins": 9},
    # Canvas collectible actions
    "Collected plastic bottle": {"co2": 0.3, "xp": 15, "coins": 5},
    "Planted canvas seed": {"co2": 1.5, "xp": 30, "coins": 10},
    "Turned off canvas lightbulb": {"co2": 0.4, "xp": 20, "coins": 6}
}

# Shop items catalogue
SHOP_CATALOG = {
    "skins": {
        "solar": {"name": "Solar Cape", "cost": 50, "color": "#f1c40f"},
        "cyber": {"name": "Cyber Eco Blue", "cost": 100, "color": "#3498db"},
        "earth": {"name": "Earth Guardian", "cost": 200, "color": "#e74c3c"}
    },
    "pets": {
        "leafy": {"name": "Leafy the Fox", "cost": 80, "emoji": "🦊"},
        "sparky": {"name": "Sparky the Bird", "cost": 150, "emoji": "⚡"},
        "bubbles": {"name": "Bubbles the Otter", "cost": 250, "emoji": "🦦"}
    }
}

# Helpers
def get_current_user():
    if "username" not in session:
        return None
    return db.users.find_one({"username": session["username"]})

def serialize_doc(doc):
    if not doc:
        return None
    doc_copy = dict(doc)
    if "_id" in doc_copy:
        doc_copy["_id"] = str(doc_copy["_id"])
    if "password_hash" in doc_copy:
        del doc_copy["password_hash"]
    return doc_copy

def calculate_stage(level):
    if 1 <= level <= 3:
        return "Polluted City"
    elif 4 <= level <= 6:
        return "Recovering Forest"
    elif 7 <= level <= 9:
        return "Clean River Valley"
    elif 10 <= level <= 12:
        return "Mountain Sanctuary"
    else:
        return "Sustainable Future City"

def check_and_award_achievements(user):
    achievements = list(user.get("achievements", []))
    new_achievements = []
    
    # First Step: first eco action logged
    action_count = db.actions.count_documents({"user_id": str(user["_id"])})
    if action_count >= 1 and "First Step" not in achievements:
        new_achievements.append("First Step")
        
    # Recycler: 10 recycling actions
    recycling_count = db.actions.count_documents({
        "user_id": str(user["_id"]),
        "action_name": {"$or": [{"action_name": "Recycling waste"}, {"action_name": "Collected plastic bottle"}]} if hasattr(db.actions, "_match") else "Recycling waste" # simple match for mock vs pymongo
    })
    # Fallback checking for mock db which doesn't support complex nested queries easily
    if action_count > 0 and "Recycler" not in achievements:
        # manual count fallback to ensure 100% reliability
        actions_list = db.actions.find({"user_id": str(user["_id"])})
        recycle_actions = [a for a in actions_list if "Recycling" in a["action_name"] or "bottle" in a["action_name"]]
        if len(recycle_actions) >= 10:
            new_achievements.append("Recycler")
            
    # Walker: 50 km walked. Let's count walking actions. Let's assume each walking action is 2km
    if "Walker" not in achievements:
        actions_list = db.actions.find({"user_id": str(user["_id"])})
        walk_actions = [a for a in actions_list if "Walking" in a["action_name"]]
        # Let's say if total walks * 2 >= 50
        if len(walk_actions) * 2 >= 50:
            new_achievements.append("Walker")
            
    # Carbon Hero: 100 kg CO2 saved
    if user.get("total_co2_saved", 0.0) >= 100.0 and "Carbon Hero" not in achievements:
        new_achievements.append("Carbon Hero")
        
    # Earth Guardian: Completed all stages (reaches Level 13+)
    if user.get("level", 1) >= 13 and "Earth Guardian" not in achievements:
        new_achievements.append("Earth Guardian")
        
    return new_achievements

def generate_daily_quests(user_id):
    # Daily quest items pool
    quest_pool = [
        {"name": "Use public transport today", "reward_xp": 40, "reward_coins": 10},
        {"name": "Carry a reusable bottle", "reward_xp": 20, "reward_coins": 5},
        {"name": "Reduce electricity consumption", "reward_xp": 45, "reward_coins": 8},
        {"name": "Walk 2 km instead of driving", "reward_xp": 50, "reward_coins": 12},
        {"name": "Plant a tree", "reward_xp": 100, "reward_coins": 25},
        {"name": "Eat a vegetarian lunch", "reward_xp": 60, "reward_coins": 15}
    ]
    
    # Delete old uncompleted quests
    db.quests.delete_many({"user_id": user_id, "completed": False})
    
    # Select 3 random quests
    selected = random.sample(quest_pool, 3)
    quests_to_insert = []
    for q in selected:
        quests_to_insert.append({
            "user_id": user_id,
            "quest_name": q["name"],
            "reward_xp": q["reward_xp"],
            "reward_coins": q["reward_coins"],
            "completed": False,
            "timestamp": datetime.datetime.utcnow().isoformat()
        })
        
    db.quests.insert_many(quests_to_insert)

# Static Routes
@app.route("/")
def index():
    return render_template("index.html")

# Auth APIs
@app.route("/api/auth/register", methods=["POST"])
def register():
    data = request.json or {}
    username = data.get("username", "").strip()
    email = data.get("email", "").strip()
    password = data.get("password", "")
    
    if not username or not email or not password:
        return jsonify({"error": "All fields are required"}), 400
        
    if db.users.find_one({"username": username}):
        return jsonify({"error": "Username already exists"}), 400
        
    user_doc = {
        "username": username,
        "email": email,
        "password_hash": generate_password_hash(password),
        "level": 1,
        "xp": 0,
        "eco_coins": 100,  # 100 start coins so user can buy something early
        "stage": "Polluted City",
        "total_co2_saved": 0.0,
        "achievements": [],
        "unlocked_skins": ["default"],
        "unlocked_pets": [],
        "active_skin": "default",
        "active_pet": ""
    }
    
    res = db.users.insert_one(user_doc)
    session["username"] = username
    
    # Generate initial quests
    generate_daily_quests(str(res.inserted_id))
    
    return jsonify({"success": "Registration successful", "user": serialize_doc(user_doc)})

@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.json or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    
    if not username or not password:
        return jsonify({"error": "All fields are required"}), 400
        
    user = db.users.find_one({"username": username})
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Invalid username or password"}), 401
        
    session["username"] = username
    
    # Refresh/check quests
    quests_count = db.quests.count_documents({"user_id": str(user["_id"]), "completed": False})
    if quests_count == 0:
        generate_daily_quests(str(user["_id"]))
        
    return jsonify({"success": "Login successful", "user": serialize_doc(user)})

@app.route("/api/auth/logout", methods=["POST"])
def logout():
    session.pop("username", None)
    return jsonify({"success": "Logged out successfully"})

# Profile API
@app.route("/api/user/profile", methods=["GET"])
def profile():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify(serialize_doc(user))

# Action API
@app.route("/api/user/log_action", methods=["POST"])
def log_action():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
        
    data = request.json or {}
    action_name = data.get("action_name", "")
    
    # Find matching configuration
    config = ACTION_CONFIGS.get(action_name)
    if not config:
        # Allow logging custom actions with basic rewards
        config = {"co2": 0.5, "xp": 20, "coins": 5}
        
    xp_earned = config["xp"]
    co2_saved = config["co2"]
    coins_earned = config["coins"]
    
    # Save action log
    action_doc = {
        "user_id": str(user["_id"]),
        "action_name": action_name,
        "xp_earned": xp_earned,
        "co2_saved": co2_saved,
        "timestamp": datetime.datetime.utcnow().isoformat()
    }
    db.actions.insert_one(action_doc)
    
    # Update user state
    new_xp = user.get("xp", 0) + xp_earned
    new_co2 = round(user.get("total_co2_saved", 0.0) + co2_saved, 2)
    new_coins = user.get("eco_coins", 0) + coins_earned
    current_level = user.get("level", 1)
    
    # Check level up: XP limit = current_level * 100
    leveled_up = False
    while new_xp >= (current_level * 100):
        new_xp -= (current_level * 100)
        current_level += 1
        leveled_up = True
        
    new_stage = calculate_stage(current_level)
    
    # Temporary update to check achievements
    temp_user = dict(user)
    temp_user["level"] = current_level
    temp_user["xp"] = new_xp
    temp_user["total_co2_saved"] = new_co2
    temp_user["stage"] = new_stage
    
    new_badges = check_and_award_achievements(temp_user)
    all_achievements = list(user.get("achievements", []))
    if new_badges:
        all_achievements.extend(new_badges)
        
    # Commit changes
    db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {
            "xp": new_xp,
            "level": current_level,
            "eco_coins": new_coins,
            "stage": new_stage,
            "total_co2_saved": new_co2,
            "achievements": all_achievements
        }}
    )
    
    # Fetch updated user
    updated_user = db.users.find_one({"_id": user["_id"]})
    
    return jsonify({
        "success": "Action logged successfully",
        "xp_earned": xp_earned,
        "co2_saved": co2_saved,
        "coins_earned": coins_earned,
        "leveled_up": leveled_up,
        "unlocked_badges": new_badges,
        "user": serialize_doc(updated_user)
    })

# Quests APIs
@app.route("/api/user/quests", methods=["GET"])
def get_quests():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
        
    quests = db.quests.find({"user_id": str(user["_id"])})
    return jsonify([serialize_doc(q) for q in quests])

@app.route("/api/user/quests/complete", methods=["POST"])
def complete_quest():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
        
    data = request.json or {}
    quest_id = data.get("quest_id")
    if not quest_id:
        return jsonify({"error": "Quest ID required"}), 400
        
    quest = db.quests.find_one({"_id": quest_id, "user_id": str(user["_id"])})
    if not quest:
        return jsonify({"error": "Quest not found"}), 404
        
    if quest.get("completed"):
        return jsonify({"error": "Quest already completed"}), 400
        
    reward_xp = quest.get("reward_xp", 0)
    reward_coins = quest.get("reward_coins", 0)
    
    # Mark completed
    db.quests.update_one({"_id": quest_id}, {"$set": {"completed": True}})
    
    # Add rewards to user
    new_xp = user.get("xp", 0) + reward_xp
    new_coins = user.get("eco_coins", 0) + reward_coins
    current_level = user.get("level", 1)
    
    leveled_up = False
    while new_xp >= (current_level * 100):
        new_xp -= (current_level * 100)
        current_level += 1
        leveled_up = True
        
    new_stage = calculate_stage(current_level)
    
    temp_user = dict(user)
    temp_user["level"] = current_level
    temp_user["xp"] = new_xp
    temp_user["stage"] = new_stage
    
    new_badges = check_and_award_achievements(temp_user)
    all_achievements = list(user.get("achievements", []))
    if new_badges:
        all_achievements.extend(new_badges)
        
    db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {
            "xp": new_xp,
            "level": current_level,
            "eco_coins": new_coins,
            "stage": new_stage,
            "achievements": all_achievements
        }}
    )
    
    updated_user = db.users.find_one({"_id": user["_id"]})
    
    return jsonify({
        "success": "Quest completed!",
        "xp_earned": reward_xp,
        "coins_earned": reward_coins,
        "leveled_up": leveled_up,
        "unlocked_badges": new_badges,
        "user": serialize_doc(updated_user)
    })

# Shop APIs
@app.route("/api/user/shop/buy", methods=["POST"])
def shop_buy():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
        
    data = request.json or {}
    item_type = data.get("item_type")  # "skins" or "pets"
    item_id = data.get("item_id")
    
    if item_type not in ["skins", "pets"] or item_id not in SHOP_CATALOG.get(item_type, {}):
        return jsonify({"error": "Invalid item or item type"}), 400
        
    item_data = SHOP_CATALOG[item_type][item_id]
    cost = item_data["cost"]
    
    # Check if already unlocked
    unlocked_list_name = f"unlocked_{item_type}"
    unlocked_items = user.get(unlocked_list_name, [])
    if item_id in unlocked_items:
        return jsonify({"error": "Item already unlocked"}), 400
        
    # Check coins
    current_coins = user.get("eco_coins", 0)
    if current_coins < cost:
        return jsonify({"error": "Not enough Eco Coins"}), 400
        
    # Deduct coins and unlock
    new_coins = current_coins - cost
    db.users.update_one(
        {"_id": user["_id"]},
        {
            "$set": {"eco_coins": new_coins},
            "$push": {unlocked_list_name: item_id}
        }
    )
    
    updated_user = db.users.find_one({"_id": user["_id"]})
    return jsonify({
        "success": f"Unlocked {item_data.get('name') or item_id}!",
        "user": serialize_doc(updated_user)
    })

@app.route("/api/user/shop/equip", methods=["POST"])
def shop_equip():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
        
    data = request.json or {}
    item_type = data.get("item_type")  # "skin" or "pet"
    item_id = data.get("item_id")
    
    if item_type not in ["skin", "pet"]:
        return jsonify({"error": "Invalid item type"}), 400
        
    # Equip logic
    if item_id != "":
        # verify it is unlocked
        unlocked_list_name = "unlocked_skins" if item_type == "skin" else "unlocked_pets"
        unlocked_items = user.get(unlocked_list_name, [])
        if item_id not in unlocked_items:
            return jsonify({"error": "Item not unlocked yet"}), 400
            
    field_to_set = f"active_{item_type}"
    db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {field_to_set: item_id}}
    )
    
    updated_user = db.users.find_one({"_id": user["_id"]})
    return jsonify({
        "success": f"Equipped {item_type}!",
        "user": serialize_doc(updated_user)
    })

# Leaderboard API
@app.route("/api/leaderboard", methods=["GET"])
def get_leaderboard():
    users_cursor = db.users.find({})
    leaderboard_data = []
    for u in users_cursor:
        leaderboard_data.append({
            "username": u["username"],
            "level": u.get("level", 1),
            "xp": u.get("xp", 0),
            "total_co2_saved": u.get("total_co2_saved", 0.0)
        })
        
    # Sort by total_co2_saved desc, then xp desc
    leaderboard_data.sort(key=lambda x: (x["total_co2_saved"], x["xp"]), reverse=True)
    
    # Add ranks
    for idx, item in enumerate(leaderboard_data):
        item["rank"] = idx + 1
        
    return jsonify(leaderboard_data[:50])

# Smart Assistant API
@app.route("/api/assistant/tips", methods=["GET"])
def get_assistant_tips():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
        
    # Analyze recent activities
    recent_actions = list(db.actions.find({"user_id": str(user["_id"])}, sort=[("timestamp", -1)], limit=10))
    action_names = [a["action_name"] for a in recent_actions]
    
    tips = []
    
    # Check which actions are missing
    if "Eating vegetarian meals" not in action_names:
        tips.append({
            "text": "Eating vegetarian just once a week can reduce your greenhouse gas emissions significantly.",
            "impact": "Replacing a meat meal with a vegetarian option saves approximately 1.5 kg of CO₂ and earns 60 XP!"
        })
    if "Saving electricity" not in action_names:
        tips.append({
            "text": "Turn off unused room lights, TVs, and monitors when not in use.",
            "impact": "Conserving electricity for a few hours saves 0.6 kg of CO₂ and earns 45 XP!"
        })
    if "Using public transportation" not in action_names and "Walking instead of driving" not in action_names:
        tips.append({
            "text": "Leaving your car at home and walking or riding public transit is the fastest way to shrink your footprint.",
            "impact": "Replacing a personal car drive saves 0.8 kg to 1.2 kg of CO₂ and earns up to 50 XP!"
        })
        
    # Default tips if user logs are balanced
    if len(tips) < 2:
        tips.append({
            "text": "Try carrying a reusable water bottle or travel coffee mug everywhere you go.",
            "impact": "Avoiding single-use plastics saves 0.2 kg of CO₂ and earns 20 XP."
        })
        tips.append({
            "text": "Consider planting local trees or wild flowering plants in your garden to clean the air.",
            "impact": "A single tree absorbs carbon over its lifetime, giving you 5.0 kg of CO₂ savings and 100 XP!"
        })
        
    # Custom recommendation based on user level and stage
    rec = f"Eco Guardian, you are currently level {user.get('level')} in the {user.get('stage')}. "
    if user.get("level") < 4:
        rec += "To restore the city and unlock the Recovering Forest stage, log 3 green actions today!"
    elif user.get("level") < 7:
        rec += "The forest is recovering, but needs water. Play the game canvas and plant some seeds to unlock the River Valley!"
    else:
        rec += "Keep up the excellent work! You are well on your way to creating a Sustainable Future City."
        
    # Select 2 tips
    selected_tips = tips[:2]
    
    return jsonify({
        "status": "success",
        "tips": selected_tips,
        "recommendation": rec,
        "savings_opportunity": "Replacing 2 weekly car trips with public transit saves ~12 kg CO₂/week."
    })

# Actions History API
@app.route("/api/user/actions", methods=["GET"])
def get_user_actions():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    actions = db.actions.find({"user_id": str(user["_id"])}, sort=[("timestamp", -1)], limit=15)
    return jsonify([serialize_doc(a) for a in actions])

if __name__ == "__main__":
    app.run(debug=True, port=5000)
