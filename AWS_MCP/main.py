import os
from dotenv import load_dotenv
from fastmcp import FastMCP
import psycopg2
from fastmcp.server.dependencies import get_http_headers
import jwt
from jwt import PyJWKClient
import logging


logger = logging.getLogger(__name__)

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
CLERK_JWKS_URL = "https://maximum-python-2899.clerk.accounts.dev/.well-known/jwks.json"
_jwks_client = PyJWKClient(CLERK_JWKS_URL) 
CLERK_AUDIENCE="expense-tracker-mcp"


# def _load_database_url():
#     if url := os.getenv("DATABASE_URL"):   # local dev via .env
#         return url
#     sm = boto3.client("secretsmanager", region_name="<your-region>")
#     return sm.get_secret_value(SecretId="expense-tracker/database-url")["SecretString"]

# DATABASE_URL = _load_database_url()


mcp = FastMCP("Expense Tracker")


class AuthError(Exception):
    pass


def get_connection():
    return psycopg2.connect(DATABASE_URL)


#this functions checks is this particular request coming from an authenticated Clerk user, and which user is it runs when when the LLM/Agent connects to or calls your MCP server
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
            options={"verify_aud": False},
            audience=CLERK_AUDIENCE,  # Clerk session tokens don't always set aud
        )
    except jwt.PyJWTError as e:
        logger.warning(f"[auth] token verification failed: {e}")
        raise AuthError(f"Invalid token: {e}")

    user_id = payload.get("sub")
    if not user_id:
        raise AuthError("Token missing sub claim")

    return user_id


@mcp.tool()
def add_expense(amount, category, description="", expense_date=None):
    """Add a new expense entry to the database."""
    try:
           user_id = get_current_user_id()
    except AuthError as e:
           return {"status": "error", "message": str(e)}

    try:    
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

    except psycopg2.Error:
        logger.exception("Database error while adding expense")

        return {
            "status": "error",
            "message": "Failed to add expense",
        }


@mcp.tool()
def list_expenses(start_date=None, end_date=None):
    """List expenses, optionally within a date range."""
    try:
           user_id = get_current_user_id()
    except AuthError as e:
           return {"status": "error", "message": str(e)}

    try:   
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
            
    except psycopg2.Error:
        logger.exception("Database error while listing expenses")

        return {
            "status": "error",
            "message": "Failed to retrieve expenses",
        }
    


@mcp.tool()
def get_summary(start_date, end_date, category=None):
    """Summarize expenses by category within an inclusive date range."""
    try:
           user_id = get_current_user_id()
    except AuthError as e:
           return {"status": "error", "message": str(e)}

    try :    
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

    except psycopg2.Error:
        logger.exception("Database error while getting expense summary")

        return {
            "status": "error",
            "message": "Failed to get expense summary",
        } 



@mcp.tool()
def delete_expense(expense_id):
    """Delete an expense entry."""
    try:
           user_id = get_current_user_id()
    except AuthError as e:
           return {"status": "error", "message": str(e)}

    try :    
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

    except psycopg2.Error:
        logger.exception("Database error while deleting expense")

        return {
            "status": "error",
            "message": "Failed to delete expense",
        }


if __name__ == "__main__":
    
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000 , stateless_http =True)  #stateless_http = True means that the server will not maintain any session state between requests. Each request is treated independently, and the server does not store any information about previous requests or sessions.

















