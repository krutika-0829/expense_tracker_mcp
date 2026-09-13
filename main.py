import os

from dotenv import load_dotenv
from fastmcp import FastMCP
import psycopg2

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

mcp = FastMCP("Expense Tracker")


def get_connection():
    return psycopg2.connect(DATABASE_URL)


from fastmcp.server.dependencies import get_http_headers

import logging
logger = logging.getLogger("uvicorn.error")  # shows up in Render logs

@mcp.tool()
async def debug_auth() -> dict:
    headers = get_http_headers()
    auth_header = headers.get("authorization")
    logger.info(f"[debug_auth] authorization present: {auth_header is not None}, length: {len(auth_header) if auth_header else 0}")
    return {
        "authorization_present": auth_header is not None,
        "authorization_length": len(auth_header) if auth_header else 0,
    }


@mcp.tool()
def add_expense(user_id, amount, category, description="", expense_date=None):
    """Add a new expense entry to the database."""

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
def list_expenses(user_id, start_date=None, end_date=None):
    """List expenses, optionally within a date range."""

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
def get_summary(user_id, start_date, end_date, category=None):
    """Summarize expenses by category within an inclusive date range."""

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
def delete_expense(user_id, expense_id):
    """Delete an expense entry."""

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