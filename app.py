# app.py
import streamlit as st
from datetime import datetime
import sqlite3
import hashlib
import uuid
from gtts import gTTS
import io
import google.generativeai as genai

# ---------------- CONFIG ----------------
st.set_page_config(page_title="Emora — Decision Box", layout="centered")
DB_PATH = "emora.db"

# ---------------- DATABASE ----------------
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        name TEXT,
        age INTEGER,
        dob TEXT,
        guardian_email TEXT,
        guardian_phone TEXT,
        username TEXT UNIQUE,
        password_hash TEXT,
        created_at TEXT
    )""")

    conn.execute("""
    CREATE TABLE IF NOT EXISTS chats (
        id TEXT PRIMARY KEY,
        user_id TEXT,
        title TEXT,
        category TEXT,
        created_at TEXT
    )""")

    conn.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        chat_id TEXT,
        role TEXT,
        content TEXT,
        created_at TEXT
    )""")

    conn.commit()
    return conn

def hash_password(p):
    return hashlib.sha256(p.encode()).hexdigest()

# ---------------- GEMINI v1 ----------------
def call_gemini(messages, system_prompt):
    api_key = st.secrets.get("gemini_api_key", None)
    if not api_key:
        return "Missing Gemini API key in secrets.toml!"

    genai.configure(api_key=api_key)

    try:
        # ⭐ WORKING MODEL for YOUR ACCOUNT
        model = genai.GenerativeModel("models/gemini-2.5-flash")

        # Build prompt
        prompt = system_prompt + "\n\n"
        for msg in messages:
            prompt += f"{msg['role'].upper()}: {msg['content']}\n"
        prompt += "ASSISTANT:"

        response = model.generate_content(prompt)
        return response.text

    except Exception as e:
        return f"[Gemini Error] {e}"


# ---------------- HELPERS ----------------
def now():
    return datetime.utcnow().isoformat()

# ---------------- START APP ----------------
conn = get_conn()
st.title("Emora — Decision Box 🌱")

# Session state
if "user" not in st.session_state:
    st.session_state.user = None
if "cur_chat_id" not in st.session_state:
    st.session_state.cur_chat_id = None
if "category" not in st.session_state:
    st.session_state.category = "education"

# NAV
menu = ["Welcome", "Login", "Signup", "Chat"]
page = st.sidebar.selectbox("Navigation", menu)

# ---------------- WELCOME ----------------
if page == "Welcome":
    st.header("Welcome to Emora 🌱")
    st.markdown("> _“Every choice shapes your path.”_")

# ---------------- SIGNUP ----------------
elif page == "Signup":
    st.header("Create Account")

    with st.form("signup"):
        name = st.text_input("Full name")
        age = st.number_input("Age", 1, 120)
        dob = st.date_input("DOB")
        gem = st.text_input("Guardian Email")
        gph = st.text_input("Guardian Phone")
        username = st.text_input("Username")
        password = st.text_input("Password (min 6 chars)", type="password")
        submit = st.form_submit_button("Create")

    if submit:
        if age < 13:
            st.error("Must be at least 13 to sign up.")
        elif len(password) < 6:
            st.error("Password must be 6 chars+")
        else:
            try:
                uid = str(uuid.uuid4())
                conn.execute("""
                    INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (uid, name, age, dob.isoformat(), gem, gph,
                      username, hash_password(password), now()))
                conn.commit()
                st.success("Account created! Go login.")
            except sqlite3.IntegrityError:
                st.error("Username already exists.")

# ---------------- LOGIN ----------------
elif page == "Login":
    st.header("Login")

    with st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        login = st.form_submit_button("Login")

    if login:
        cur = conn.cursor()
        cur.execute("SELECT id, name, password_hash FROM users WHERE username=?", (username,))
        row = cur.fetchone()

        if not row:
            st.error("User not found.")
        elif row[2] != hash_password(password):
            st.error("Wrong password.")
        else:
            st.session_state.user = {
                "id": row[0],
                "name": row[1],
                "username": username
            }
            st.success("Login successful!")
            st.rerun()

# ---------------- CHAT ----------------
elif page == "Chat":
    if not st.session_state.user:
        st.warning("Please log in first.")
        st.stop()

    user = st.session_state.user
    st.sidebar.subheader(f"Logged in as {user['username']}")

    cur = conn.cursor()
    cur.execute("SELECT id, title, category FROM chats WHERE user_id=? ORDER BY created_at DESC",
                (user["id"],))
    chats = cur.fetchall()

    chat_list = ["+ New Chat"] + [f"{c[1]} — {c[2]}" for c in chats]
    selected = st.sidebar.selectbox("Chats", chat_list)

    if selected == "+ New Chat":
        with st.sidebar.form("newchat"):
            title = st.text_input("Chat title", f"Chat {len(chats)+1}")
            cat = st.selectbox("Category", ["education", "ethical", "personal", "emotional"])
            make = st.form_submit_button("Create")

        if make:
            cid = str(uuid.uuid4())
            conn.execute("""
                INSERT INTO chats VALUES (?, ?, ?, ?, ?)
            """, (cid, user["id"], title, cat, now()))
            conn.commit()
            st.session_state.cur_chat_id = cid
            st.session_state.category = cat
            st.rerun()

    else:
        idx = chat_list.index(selected) - 1
        cid, title, cat = chats[idx]
        st.session_state.cur_chat_id = cid
        st.session_state.category = cat

    # Show messages
    cid = st.session_state.cur_chat_id

    if cid:
        st.subheader("Conversation")

        cur.execute("SELECT role, content FROM messages WHERE chat_id=? ORDER BY created_at",
                    (cid,))
        for r, c in cur.fetchall():
            if r == "user":
                st.markdown(f"**You:** {c}")
            else:
                st.markdown(f"**Emora:** {c}")

    # Input
    user_input = st.text_input("Your message")
    send = st.button("Send")

    if send and user_input:
        categories = {
            "education": "You are Emora, an education guide.",
            "ethical": "You are Emora, an ethics advisor.",
            "personal": "You are Emora, a personal assistant.",
            "emotional": "You are Emora, an emotional support guide."
        }

        system_prompt = categories[st.session_state.category]

        # gather history
        cur.execute("SELECT role, content FROM messages WHERE chat_id=?", (cid,))
        history = [{"role": r, "content": c} for r, c in cur.fetchall()]

        reply = call_gemini(history + [{"role": "user", "content": user_input}], system_prompt)

        conn.execute("INSERT INTO messages VALUES (?, ?, ?, ?, ?)",
                     (str(uuid.uuid4()), cid, "user", user_input, now()))
        conn.execute("INSERT INTO messages VALUES (?, ?, ?, ?, ?)",
                     (str(uuid.uuid4()), cid, "assistant", reply, now()))
        conn.commit()

        st.rerun()

# ---------------- FOOTER ----------------
st.markdown("---")
st.caption("Emora © 2025 — Powered by Gemini 1.5 Flash (Stable API)")