import os


# Browser-accessible backend origin; also used as the Steam OpenID realm.
APP_PUBLIC_URL = os.getenv("APP_PUBLIC_URL", "http://localhost:8000").rstrip("/")

STEAM_CALLBACK_URL = os.getenv(
    "STEAM_CALLBACK_URL",
    f"{APP_PUBLIC_URL}/api/v1/steam/callback",
)

STEAM_OPENID_URL = "https://steamcommunity.com/openid/login"
STEAM_API_KEY = os.getenv("STEAM_API_KEY", "")
