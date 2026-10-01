import asyncio
import sys
import os
import psycopg2
from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage 
from fastapi import FastAPI, Header
import asyncio
import json
from claude_agent_sdk import SessionKey, SessionStoreEntry
import time
from dotenv import load_dotenv
load_dotenv()


#"If this program is running on Windows, configure asyncio to use the Windows Proactor event loop,
# which provides the Windows-specific async I/O behavior needed by some networking/subprocess operations(here its claudesdkagent)."
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

app = FastAPI()

DATABASE_URL = os.getenv("DATABASE_URL")


def get_connection():
    return psycopg2.connect(DATABASE_URL)


#this function retrieves the saved session ID for a given user from the db, if it exists else returns None
def get_saved_session_id(user_id: str) -> str | None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT session_id FROM user_sessions WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            return row[0] if row else None


#this function saves the session ID for a given user in the db, if it already exists it updates it
def save_session_id(user_id: str, session_id: str):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_sessions (user_id, session_id, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (user_id)
                DO UPDATE SET session_id = EXCLUDED.session_id, updated_at = now()
                """,
                (user_id, session_id),
            )




class PostgresSessionStore:
    def __init__(self, get_connection_fn):
        self._get_connection = get_connection_fn

    async def append(self, key: SessionKey, entries: list[SessionStoreEntry]) -> None:
        await asyncio.to_thread(self._append_sync, key, entries)

    def _append_sync(self, key: SessionKey, entries: list[SessionStoreEntry]) -> None:
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                for entry in entries:
                    entry_uuid = entry.get("uuid") if isinstance(entry, dict) else None
                    cur.execute(
                        """
                        INSERT INTO session_store_entries
                            (project_key, session_id, subpath, entry_uuid, entry)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (project_key, session_id, COALESCE(subpath, ''), entry_uuid)
                        WHERE entry_uuid IS NOT NULL
                        DO NOTHING
                        """,
                        (
                            key["project_key"],
                            key["session_id"],
                            key.get("subpath"),
                            entry_uuid,
                            json.dumps(entry),
                        ),
                    )

    async def load(self, key: SessionKey) -> list[SessionStoreEntry] | None:
        return await asyncio.to_thread(self._load_sync, key)

    def _load_sync(self, key: SessionKey) -> list[SessionStoreEntry] | None:
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT entry FROM session_store_entries
                    WHERE project_key = %s AND session_id = %s
                      AND subpath IS NOT DISTINCT FROM %s
                    ORDER BY id ASC
                    """,
                    (key["project_key"], key["session_id"], key.get("subpath")),
                )
                rows = cur.fetchall()
                if not rows:
                    return None
                return [row[0] for row in rows]  # jsonb comes back already parsed




session_store = PostgresSessionStore(get_connection)

async def run_agent(prompt: str, clerk_token: str, user_id: str):
    existing_session_id = get_saved_session_id(user_id)

    
    options_kwargs = dict(

        system_prompt = "You are a helpful assistant that manages personal expenses." \
        " You can add, list, summarize, and delete expenses for the user." \
        " Don't entertain any other requests unrelated to expense tracking.",
        mcp_servers={
            "expense-tracker": {
                "type": "http",
                "url": os.getenv("MCP_URL"),
                "headers": {"Authorization": f"Bearer {clerk_token}"},
            }
        },
        allowed_tools=[
          "mcp__expense-tracker__*"
        ],

        session_store=session_store, 
    )
    

    if existing_session_id:
        options_kwargs["resume"] = existing_session_id  #adds a resume option to  options_kwargs dictionary.

    options = ClaudeAgentOptions(**options_kwargs)
    result = None
    new_session_id = None
    print("Agent starting")


    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            result = message.result
            new_session_id = message.session_id

    if new_session_id:
        save_session_id(user_id, new_session_id)

    return result




import time
@app.get("/ask")
async def ask(prompt: str, authorization: str = Header(...)):
    clerk_token = authorization.replace("Bearer ", "")
  
    import jwt
    payload = jwt.decode(clerk_token, options={"verify_signature": False})
    user_id = payload.get("sub")

    result = await run_agent(prompt, clerk_token, user_id)

    print("CLERK TOKEN CLAIMS:")
    print(payload)
    print("TOKEN EXP:", payload.get("exp"))
    print("CURRENT TIME:", int(time.time()))
    print("FastAPI received token, expires in:",
      payload["exp"] - int(time.time()))
    return {"result": result}

    

