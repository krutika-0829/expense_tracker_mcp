import os
from dotenv import load_dotenv
from fastmcp import FastMCP
import psycopg2
import logging
logger = logging.getLogger("uvicorn.error")  # shows up in Render logs
from fastmcp.server.dependencies import get_http_headers
import jwt
from jwt import PyJWKClient

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

mcp = FastMCP("Expense Tracker")


def get_connection():
    return psycopg2.connect(DATABASE_URL)



CLERK_JWKS_URL = os.getenv("CLERK_JWKS_URL")
_jwks_client = PyJWKClient(CLERK_JWKS_URL)


class AuthError(Exception):
    pass


def get_current_user_id() -> str:
    """Extract and verify the Clerk session token from the incoming request,
    returning the authenticated user's id (the token's `sub` claim)."""
    headers = get_http_headers(include={"authorization"})
    auth_header = headers.get("authorization")

    if not auth_header or not auth_header.startswith("Bearer "):
        raise AuthError("Missing or malformed Authorization header")

    token = auth_header[len("Bearer "):]

    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},  # Clerk session tokens don't always set aud
        )
    except jwt.PyJWTError as e:
        raise AuthError(f"Invalid token: {e}")

    user_id = payload.get("sub")
    if not user_id:
        raise AuthError("Token missing sub claim")

    return user_id




@mcp.tool()
async def debug_auth() -> dict:
    headers = get_http_headers(include={"authorization"})
    auth_header = headers.get("authorization")
    logger.info(f"[debug_auth] authorization present: {auth_header is not None}, length: {len(auth_header) if auth_header else 0}")
    return {
        "authorization_present": auth_header is not None,
        "authorization_length": len(auth_header) if auth_header else 0,
    }


@mcp.tool()
def add_expense(amount, category, description="", expense_date=None):
    """Add a new expense entry to the database."""
    try:
           user_id = get_current_user_id()
    except AuthError as e:
           return {"status": "error", "message": str(e)}

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO expenses
                (user_id, amount, category, description, expense_date)
                VALUES (%s, %s, %s, %s, COALESCE(%s, CURRENT_DATE))
                RETURNING id
                """,
                (user_id, amount, category, description, expense_date)
            )

            expense_id = cur.fetchone()[0]

            return {"status": "ok", "id": expense_id}


@mcp.tool()
def list_expenses(start_date=None, end_date=None):
    """List expenses, optionally within a date range."""
    try:
           user_id = get_current_user_id()
    except AuthError as e:
           return {"status": "error", "message": str(e)}

    with get_connection() as conn:
        with conn.cursor() as cur:

            query = """
                SELECT id, user_id, amount, category,
                       description, expense_date, created_at
                FROM expenses
                WHERE user_id = %s
            """

            params = [user_id]

            if start_date and end_date:
                query += """
                    AND expense_date BETWEEN %s AND %s
                """
                params.extend([start_date, end_date])

            query += " ORDER BY id ASC"

            cur.execute(query, params)

            rows = cur.fetchall()
            cols = [desc[0] for desc in cur.description]

            return [dict(zip(cols, row)) for row in rows]


@mcp.tool()
def get_summary(start_date, end_date, category=None):
    """Summarize expenses by category within an inclusive date range."""
    try:
           user_id = get_current_user_id()
    except AuthError as e:
           return {"status": "error", "message": str(e)}

    with get_connection() as conn:
        with conn.cursor() as cur:

            query = """
                SELECT category, SUM(amount) AS total_amount
                FROM expenses
                WHERE user_id = %s
                  AND expense_date BETWEEN %s AND %s
            """

            params = [user_id, start_date, end_date]

            if category:
                query += " AND category = %s"
                params.append(category)

            query += """
                GROUP BY category
                ORDER BY category ASC
            """

            cur.execute(query, params)

            rows = cur.fetchall()
            cols = [desc[0] for desc in cur.description]

            return [dict(zip(cols, row)) for row in rows]


@mcp.tool()
def delete_expense(expense_id):
    """Delete an expense entry."""
    try:
           user_id = get_current_user_id()
    except AuthError as e:
           return {"status": "error", "message": str(e)}

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM expenses
                WHERE id = %s
                  AND user_id = %s
                RETURNING id
                """,
                (expense_id, user_id)
            )

            deleted = cur.fetchone()

            if deleted:
                return {"status": "ok", "deleted_id": deleted[0]}

            return {
                "status": "error",
                "message": "Expense not found"
            }


if __name__ == "__main__":
    
    mcp.run(transport="http", host="0.0.0.0", port=8000)