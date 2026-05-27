import os
import sqlite3
from datetime import datetime, timezone

from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth


load_dotenv()

DB_PATH = "spotify_auto_follow.db"

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI")

LISTEN_THRESHOLD = int(os.getenv("LISTEN_THRESHOLD", "2"))
CHECK_LIMIT = int(os.getenv("CHECK_LIMIT", "50"))

SCOPES = [
    "user-read-recently-played",
    "user-library-modify",
    "user-library-read",
    "user-follow-modify",
    "user-follow-read",
]


def get_spotify_client():
    return spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=REDIRECT_URI,
            scope=" ".join(SCOPES),
            open_browser=True,
        )
    )


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS artists (
            artist_id TEXT PRIMARY KEY,
            artist_uri TEXT NOT NULL,
            artist_name TEXT NOT NULL,
            listen_count INTEGER NOT NULL DEFAULT 0,
            followed INTEGER NOT NULL DEFAULT 0,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS plays (
            play_id TEXT PRIMARY KEY,
            track_id TEXT NOT NULL,
            track_name TEXT NOT NULL,
            played_at TEXT NOT NULL
        )
        """
    )

    conn.commit()
    conn.close()


def play_already_processed(conn, play_id):
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM plays WHERE play_id = ?", (play_id,))
    return cur.fetchone() is not None


def save_play(conn, play_id, track_id, track_name, played_at):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO plays (play_id, track_id, track_name, played_at)
        VALUES (?, ?, ?, ?)
        """,
        (play_id, track_id, track_name, played_at),
    )
    conn.commit()


def update_artist(conn, artist_id, artist_uri, artist_name):
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT listen_count, followed FROM artists
        WHERE artist_id = ?
        """,
        (artist_id,),
    )

    row = cur.fetchone()

    if row is None:
        cur.execute(
            """
            INSERT INTO artists (
                artist_id, artist_uri, artist_name, listen_count,
                followed, first_seen, last_seen
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (artist_id, artist_uri, artist_name, 1, 0, now, now),
        )
        listen_count = 1
        followed = 0
    else:
        listen_count, followed = row
        listen_count += 1
        cur.execute(
            """
            UPDATE artists
            SET listen_count = ?, last_seen = ?, artist_name = ?, artist_uri = ?
            WHERE artist_id = ?
            """,
            (listen_count, now, artist_name, artist_uri, artist_id),
        )

    conn.commit()
    return listen_count, followed


def mark_followed(conn, artist_id):
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE artists
        SET followed = 1
        WHERE artist_id = ?
        """,
        (artist_id,),
    )
    conn.commit()


def follow_artist(sp, artist_id, artist_uri, artist_name):
    try:
        sp.user_follow_artists([artist_id])
        print(f"Followed artist: {artist_name}")
        return True

    except Exception as follow_error:
        print(f"Could not follow {artist_name}: {follow_error}")
        return False


def main():
    init_db()
    sp = get_spotify_client()

    conn = sqlite3.connect(DB_PATH)

    results = sp.current_user_recently_played(limit=CHECK_LIMIT)
    items = results.get("items", [])

    if not items:
        print("No recently played tracks found.")
        conn.close()
        return

    newly_followed = []

    for item in items:
        track = item.get("track")
        played_at = item.get("played_at")

        if not track or not played_at:
            continue

        track_id = track.get("id")
        track_name = track.get("name", "Unknown Track")

        if not track_id:
            continue

        # Unique enough for our purposes: same track + same played timestamp.
        play_id = f"{track_id}:{played_at}"

        if play_already_processed(conn, play_id):
            continue

        save_play(conn, play_id, track_id, track_name, played_at)

        artists = track.get("artists", [])

        for artist in artists:
            artist_id = artist.get("id")
            artist_uri = artist.get("uri")
            artist_name = artist.get("name", "Unknown Artist")

            if not artist_id or not artist_uri:
                continue

            listen_count, followed = update_artist(
                conn,
                artist_id,
                artist_uri,
                artist_name,
            )

            print(f"{artist_name}: {listen_count} listen(s)")

            if followed == 0 and listen_count >= LISTEN_THRESHOLD:
                success = follow_artist(sp, artist_id, artist_uri, artist_name)

                if success:
                    mark_followed(conn, artist_id)
                    newly_followed.append(artist_name)

    conn.close()

    print("")
    print("Run complete.")

    if newly_followed:
        print("Newly followed/saved artists:")
        for name in newly_followed:
            print(f"- {name}")
    else:
        print("No new artists followed/saved this run.")


if __name__ == "__main__":
    main()
