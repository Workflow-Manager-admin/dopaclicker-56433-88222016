from fastapi import FastAPI, APIRouter, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Any
import httpx
import os

# Application Metadata for OpenAPI/Swagger
app = FastAPI(
    title="Dopamine Clicker Backend API",
    description="""
        Backend API for the Dopamine Idle Clicker Game.
        - Stateless endpoints for click, upgrade, achievement, and automation logic.
        - Proxy endpoints for NewsAPI, Imgflip, and Audius.
        - Game logic and session data are transient (reset on refresh).
        - No server-side database or storage.
        - Designed for seamless integration with a React frontend.
    """,
    version="1.0.0",
    openapi_tags=[
        {"name": "game", "description": "Core game click, upgrade, and achievement endpoints."},
        {"name": "external", "description": "API proxies for news, memes, music/audio."},
        {"name": "fun", "description": "Meme generator, inbox, easter egg, and surprise endpoints."},
        {"name": "session", "description": "Transient session utilities."},
    ]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Replace with source(s) in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =====================
# Schemas & Models
# =====================

# PUBLIC_INTERFACE
class GameState(BaseModel):
    clicks: int = Field(..., description="Total click count")
    dopamine: int = Field(..., description="Current dopamine/stimulation points")
    upgrades: List[str] = Field(default_factory=list, description="List of upgrade ids unlocked")
    achievements: List[str] = Field(default_factory=list, description="List of achievement ids unlocked")
    automations: List[str] = Field(default_factory=list, description="Automation unlocks")
    last_achievement_popup: Optional[str] = Field(None, description="Last achievement shown")
    inbox: Optional[List[Dict[str, Any]]] = Field(default_factory=list, description="Inbox messages")
    overload_mode: Optional[bool] = Field(default=False, description="3M Clicks Overload Mode active")


# PUBLIC_INTERFACE
class ClickRequest(BaseModel):
    state: GameState = Field(..., description="Current game state from client")
    click_amount: int = Field(1, description="Number of clicks to simulate")


# PUBLIC_INTERFACE
class UpgradeRequest(BaseModel):
    state: GameState = Field(..., description="Current game state from client")
    upgrade_id: str = Field(..., description="Upgrade to purchase/unlock")


# PUBLIC_INTERFACE
class AchievementRequest(BaseModel):
    state: GameState = Field(..., description="Current game state from client")


# =====================
# Core Logic Constants
# =====================

BASE_CLICK_POWER = 1
UPGRADE_CONFIG = {
    # id: (name, base_cost, click_power, unlock_req_clicks, description)
    "fidget_spinner": {
        "name": "Fidget Spinner",
        "base_cost": 50,
        "click_power": 5,
        "unlock_reqs": {"clicks": 20},
        "desc": "Spin for extra dopamine!"
    },
    "led_keyboard": {
        "name": "RGB LED Keyboard",
        "base_cost": 120,
        "click_power": 12,
        "unlock_reqs": {"clicks": 60},
        "desc": "Click fancy = click faster."
    },
    "music_box": {
        "name": "Lofi Music Box",
        "base_cost": 400,
        "click_power": 25,
        "unlock_reqs": {"clicks": 200},
        "desc": "Background music raises mood!"
    },
    "auto_clicker": {
        "name": "Automation Bot",
        "base_cost": 800,
        "click_power": 0,
        "auto_power": 4,
        "unlock_reqs": {"clicks": 750},
        "desc": "Bot clicks for you!"
    },
}
# Example achievement config
ACHIEVEMENTS_CONFIG = {
    "click_100": {
        "name": "Century Clicker",
        "desc": "You clicked 100 times!",
        "unlock_reqs": {"clicks": 100}
    },
    "upgrade_fidget": {
        "name": "First Fidget",
        "desc": "Bought the Fidget Spinner Upgrade",
        "unlock_reqs": {"upgrades": ["fidget_spinner"]}
    },
    "automation_born": {
        "name": "Let Me Do It",
        "desc": "Unlocked Automation",
        "unlock_reqs": {"upgrades": ["auto_clicker"]}
    },
    "overload_mode": {
        "name": "3M Overload",
        "desc": "Hit 3,000,000 Clicks!!",
        "unlock_reqs": {"clicks": 3_000_000}
    },
    # ... more can be added
}

# =====================
# Utility Logic
# =====================

def calculate_click_power(upgrades: List[str]) -> int:
    """Calculate total manual click power based on unlocked upgrades."""
    total = BASE_CLICK_POWER
    for upg in upgrades:
        cfg = UPGRADE_CONFIG.get(upg, {})
        total += cfg.get("click_power", 0)
    return total

def calculate_automation_power(upgrades: List[str]) -> int:
    """Calculate dopamine per tick from unlocked automations."""
    auto = 0
    for upg in upgrades:
        cfg = UPGRADE_CONFIG.get(upg, {})
        auto += cfg.get("auto_power", 0)
    return auto

def next_upgrades(state: GameState):
    """Suggest list of upgrades now unlockable by click count."""
    can_buy = []
    for upg_id, data in UPGRADE_CONFIG.items():
        if upg_id not in state.upgrades and state.clicks >= data["unlock_reqs"]["clicks"]:
            can_buy.append(upg_id)
    return can_buy

def check_achievements(state: GameState) -> List[str]:
    """Return new achievements based on current state and config."""
    unlocked = set(state.achievements)
    newly = []
    for key, ach in ACHIEVEMENTS_CONFIG.items():
        if key in unlocked:
            continue
        valid = True
        # Check click requirements
        if "clicks" in ach.get("unlock_reqs", {}):
            if state.clicks < ach["unlock_reqs"]["clicks"]:
                valid = False
        # Check upgrade unlocks
        if "upgrades" in ach.get("unlock_reqs", {}):
            if not all(u in state.upgrades for u in ach["unlock_reqs"]["upgrades"]):
                valid = False
        # Add more checks here as needed
        if valid:
            newly.append(key)
    return newly

# =====================
# Routers & Endpoints
# =====================

router = APIRouter()


# --- Click Handling ---

# PUBLIC_INTERFACE
@router.post("/click", summary="Process user click events", tags=["game"])
async def process_click(req: ClickRequest):
    """
    Process a user click or series of clicks and update dopamine and click stats.
    Stateless: all state is sent from the client and new state is returned.
    """
    state = req.state
    click_amt = max(1, req.click_amount)
    new_clicks = state.clicks + click_amt
    # Calculate click power
    click_power = calculate_click_power(state.upgrades)
    dopamine_gain = click_power * click_amt
    dopamine = state.dopamine + dopamine_gain
    # Automations (simulate a tick)
    auto_dopamine = calculate_automation_power(state.upgrades)
    dopamine += auto_dopamine
    # Suggest possible upgrades
    possible_upgrades = next_upgrades(state)
    # Achievement checks
    temp_state = GameState(**dict(state), clicks=new_clicks, dopamine=dopamine)
    new_achievements = check_achievements(temp_state)
    return {
        "new_state": {
            **dict(temp_state),
            "clicks": new_clicks,
            "dopamine": dopamine,
        },
        "dopamine_gained": dopamine_gain,
        "auto_gain": auto_dopamine,
        "possible_upgrades": possible_upgrades,
        "new_achievements": new_achievements,
    }



# --- Upgrades ---

# PUBLIC_INTERFACE
@router.post("/upgrade", summary="Purchase or activate an upgrade", tags=["game"])
async def purchase_upgrade(req: UpgradeRequest):
    """
    Purchase or unlock an upgrade if available.
    Deducts dopamine if criteria met. Stateless - state always returned.
    """
    state: GameState = req.state
    upg = UPGRADE_CONFIG.get(req.upgrade_id)
    if not upg:
        raise HTTPException(status_code=400, detail="Upgrade not found")
    # Already owned?
    if req.upgrade_id in state.upgrades:
        return {"new_state": state, "message": "Already unlocked"}
    # Sufficient dopamine?
    if state.dopamine < upg["base_cost"]:
        raise HTTPException(status_code=400, detail="Not enough dopamine")
    # Unlock reqs?
    if state.clicks < upg["unlock_reqs"]["clicks"]:
        raise HTTPException(status_code=400, detail="Not enough clicks for this upgrade")
    # Purchase!
    new_dopamine = state.dopamine - upg["base_cost"]
    new_upgrades = state.upgrades + [req.upgrade_id]
    temp_state = GameState(
        **dict(state),
        dopamine=new_dopamine,
        upgrades=new_upgrades
    )
    # Check for achievements
    new_achievements = check_achievements(temp_state)
    return {
        "new_state": dict(temp_state),
        "dopamine_spent": upg["base_cost"],
        "upgrade_unlocked": req.upgrade_id,
        "new_achievements": new_achievements,
    }


# --- Achievements Display ---

# PUBLIC_INTERFACE
@router.post("/achievements", summary="Check/update for unlocked achievements", tags=["game"])
async def update_achievements(req: AchievementRequest):
    """
    Re-evaluate and return updated achievement list for current state.
    """
    state: GameState = req.state
    found = check_achievements(state)
    new_achievements = state.achievements + found
    return {
        "all_achievements": new_achievements,
        "unlocked": found
    }


# --- Inbox, Meme, and Fun Endpoints ---

# PUBLIC_INTERFACE
@router.get("/inbox", summary="Get a fun inbox message (randomized, stateless)", tags=["fun"])
async def get_inbox_message():
    """
    Receive a randomized fun message for inbox. No persistence.
    """
    import random
    inbox_samples = [
        {"title": "Congrats!", "body": "You just unlocked a new skin! (Not really, but dreams are free 😁)"},
        {"title": "Reminder", "body": "Drink water for REAL dopamine."},
        {"title": "Fun Fact", "body": "Emus can't walk backwards 🦤."}
    ]
    return random.choice(inbox_samples)

# PUBLIC_INTERFACE
@router.get("/meme", summary="Generate (proxy) a meme via Imgflip", tags=["fun", "external"])
async def get_meme(caption: str = Query(..., description="Caption for the meme")):
    """
    Generate or proxy a meme using Imgflip API.
    Replace or extend this with actual meme generator integration as needed.
    """
    # Example: Proxy public Imgflip API (not authenticated). In reality, you might need to pass a template_id and credentials.
    imgflip_url = "https://api.imgflip.com/caption_image"
    params = {
        "template_id": "112126428",  # Example: Distracted Boyfriend
        "username": os.getenv("IMGFLIP_USERNAME", "demo"),
        "password": os.getenv("IMGFLIP_PASSWORD", "demo"),
        "text0": caption,
        "text1": "",
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(imgflip_url, data=params)
        meme_result = r.json()
    if meme_result["success"]:
        return {"meme_url": meme_result["data"]["url"]}
    else:
        raise HTTPException(status_code=502, detail="Meme generation failed")


# --- News Ticker API Proxy ---

# PUBLIC_INTERFACE
@router.get("/news", summary="Proxy latest news headlines (NewsAPI)", tags=["external"])
async def get_news():
    """
    Return news headlines using NewsAPI proxy.
    Note: Set NEWSAPI_KEY as env variable for real use.
    """
    NEWS_API_KEY = os.getenv("NEWSAPI_KEY", "demo")
    news_url = "https://newsapi.org/v2/top-headlines"
    params = {"country": "us", "pageSize": 5, "apiKey": NEWS_API_KEY}
    async with httpx.AsyncClient() as client:
        resp = await client.get(news_url, params=params)
        try:
            news = resp.json()
        except Exception:
            news = {"status": "error", "articles": []}
    return {"status": news.get("status"), "articles": news.get("articles", [])}


# --- Audius/Audio Proxy ---

# PUBLIC_INTERFACE
@router.get("/audio", summary="Proxy trending tracks from Audius (Lofi, etc)", tags=["external"])
async def get_lofi_tracks():
    """
    Proxy trending lofi tracks from Audius (stateless).
    """
    audius_url = "https://discoveryprovider.audius.co/v1/tracks/trending"
    params = {
        "genre": "lo-fi", "limit": 5
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(audius_url, params=params)
        try:
            data = resp.json()
        except Exception:
            data = {"data": []}
    return {"tracks": data.get("data", [])}


# --- Overload/Easter Egg ---

# PUBLIC_INTERFACE
@router.get("/overload", summary="Trigger 3M Clicks Overload Mode", tags=["fun"])
async def overload():
    """
    Returns a message for 3M clicks overload mode.
    """
    return {
        "title": "Overload Mode!",
        "body": "You achieved 3M clicks! Prepare for visual chaos! 😵‍💫",
        "effect": "overload_mode"
    }


# --- Session Utilities (reset/game window) ---

# PUBLIC_INTERFACE
@router.get("/session", summary="Get a stateless initial session/game state", tags=["session"])
async def get_new_game_state():
    """
    Returns a fresh game state for new sessions or reset.
    """
    return GameState(
        clicks=0,
        dopamine=0,
        upgrades=[],
        automations=[],
        achievements=[],
        overload_mode=False,
        inbox=[]
    )


# --- Health/Root Endpoint ---
@app.get("/", tags=["session"])
def health_check():
    """Health check for backend service."""
    return {"message": "Healthy"}


app.include_router(router)

# --- OpenAPI Notes for WebSocket and Frontend ---
@app.get("/docs/ws_note", include_in_schema=True, tags=["session"])
def ws_note():
    """
    WebSocket/API real-time note (simulate, see frontend for actual impl).
    """
    return {
        "note": "No WebSocket endpoints; game is HTTP REST stateless. All real-time sync is handled client-side."
    }
