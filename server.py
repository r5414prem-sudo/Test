from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime, timedelta
import os
import psycopg2
from psycopg2.extras import RealDictCursor
import threading
import time
import json
from mega import Mega

app = Flask(__name__)
CORS(app)

# 👑 RANK SYSTEM - YOUR ACTUAL USERNAMES
RANKS = {
    "foffasfieifro": {"rank": "Owner", "emoji": "👑", "color": "#FFD700", "level": 3},
    "Ya_shumi09": {"rank": "Owner", "emoji": "👑", "color": "#FFD700", "level": 3},
    "shimul2222222": {"rank": "Mod", "emoji": "🛡️", "color": "#4ECDC4", "level": 2},
}

DEFAULT_RANK = {"rank": "Member", "emoji": "👤", "color": "#CCCCCC", "level": 0}

# Mega.nz Configuration
MEGA_EMAIL = os.environ.get('MEGA_EMAIL')  # Set in Render environment variables
MEGA_PASSWORD = os.environ.get('MEGA_PASSWORD')  # Set in Render environment variables

# Banned users list (muted)
BANNED_USERS = set()

def get_user_rank(username):
    """Get rank info for a user"""
    return RANKS.get(username, DEFAULT_RANK)

def is_staff(username):
    """Check if user is staff (Owner or Mod)"""
    rank_info = get_user_rank(username)
    return rank_info.get('level', 0) >= 2

def is_owner(username):
    """Check if user is Owner"""
    rank_info = get_user_rank(username)
    return rank_info.get('level', 0) >= 3

# Database connection
DATABASE_URL = os.environ.get('DATABASE_URL')

def get_db_connection():
    """Create database connection"""
    if not DATABASE_URL:
        return None
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        print(f"Database connection error: {e}")
        return None

def init_database():
    """Initialize database tables if they don't exist"""
    if not DATABASE_URL:
        print("⚠️  WARNING: DATABASE_URL not set - skipping database initialization")
        return False
    
    try:
        conn = get_db_connection()
        if not conn:
            print("❌ Could not connect to database")
            return False
            
        cur = conn.cursor()
        
        # Create messages table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) NOT NULL,
                message TEXT NOT NULL,
                game VARCHAR(100) NOT NULL,
                rank VARCHAR(50),
                rank_emoji VARCHAR(10),
                rank_color VARCHAR(7),
                room VARCHAR(10),
                message_type VARCHAR(20) DEFAULT 'general',
                timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create banned users table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS banned_users (
                username VARCHAR(50) PRIMARY KEY,
                banned_by VARCHAR(50),
                reason TEXT,
                banned_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create active users table (for tracking who's online)
        cur.execute('''
            CREATE TABLE IF NOT EXISTS active_users (
                username VARCHAR(50) PRIMARY KEY,
                game VARCHAR(100),
                last_seen TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create private rooms table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS private_rooms (
                room_code VARCHAR(10) PRIMARY KEY,
                owner VARCHAR(50) NOT NULL,
                is_private BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create room members table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS room_members (
                id SERIAL PRIMARY KEY,
                room_code VARCHAR(10) NOT NULL,
                username VARCHAR(50) NOT NULL,
                joined_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(room_code, username)
            )
        ''')
        
        # Create indexes for faster queries
        cur.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp DESC)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_room ON messages(room)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_last_seen ON active_users(last_seen DESC)')
        
        conn.commit()
        cur.close()
        conn.close()
        print("✅ Database initialized successfully")
        return True
    except Exception as e:
        print(f"❌ Database initialization error: {e}")
        return False

# ============ MEGA.NZ AUTO BACKUP SYSTEM ============

def backup_to_mega():
    """Backup all chat logs to Mega.nz and clear database"""
    try:
        print("📦 Starting hourly backup to Mega.nz...")
        
        conn = get_db_connection()
        if not conn:
            print("❌ Database connection failed for backup")
            return
        
        cur = conn.cursor()
        
        # Get all messages
        cur.execute('''
            SELECT id, username, message, game, rank, rank_emoji, rank_color, 
                   room, message_type, timestamp 
            FROM messages 
            ORDER BY timestamp ASC
        ''')
        
        messages = cur.fetchall()
        
        if len(messages) == 0:
            print("ℹ️  No messages to backup")
            cur.close()
            conn.close()
            return
        
        # Convert to JSON
        backup_data = {
            "backup_time": datetime.now().isoformat(),
            "message_count": len(messages),
            "messages": []
        }
        
        for msg in messages:
            backup_data["messages"].append({
                "id": msg['id'],
                "username": msg['username'],
                "message": msg['message'],
                "game": msg['game'],
                "rank": msg['rank'],
                "rank_emoji": msg['rank_emoji'],
                "rank_color": msg['rank_color'],
                "room": msg['room'],
                "message_type": msg['message_type'],
                "timestamp": msg['timestamp'].isoformat()
            })
        
        # Create filename with timestamp
        filename = f"chat_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        # Save to temporary file
        with open(f"/tmp/{filename}", 'w') as f:
            json.dump(backup_data, f, indent=2)
        
        # Upload to Mega.nz
        if MEGA_EMAIL and MEGA_PASSWORD:
            try:
                mega = Mega()
                m = mega.login(MEGA_EMAIL, MEGA_PASSWORD)
                
                # Get or create "PrimeX Chat Backups" folder
                folder = None
                files = m.get_files()
                
                for file_id in files:
                    file_data = files[file_id]
                    if file_data['a'] and file_data['a'].get('n') == 'PrimeX Chat Backups' and file_data['t'] == 1:
                        folder = file_data
                        break
                
                # Create folder if it doesn't exist
                if not folder:
                    folder = m.create_folder('PrimeX Chat Backups')
                    print("📁 Created folder: PrimeX Chat Backups")
                
                # Upload file to the folder
                file = m.upload(f"/tmp/{filename}", folder[0] if isinstance(folder, tuple) else folder)
                print(f"✅ Backup uploaded to Mega.nz/PrimeX Chat Backups/{filename}")
                
                # Get shareable link
                link = m.get_upload_link(file)
                print(f"🔗 Backup link: {link}")
                
                # Clean up temp file
                os.remove(f"/tmp/{filename}")
                
            except Exception as e:
                print(f"❌ Mega.nz upload error: {e}")
        else:
            print("⚠️  Mega.nz credentials not set, backup saved locally only")
        
        # Clear messages from database
        cur.execute('DELETE FROM messages')
        deleted = cur.rowcount
        
        conn.commit()
        cur.close()
        conn.close()
        
        print(f"✅ Backup complete! {deleted} messages cleared from database")
        
    except Exception as e:
        print(f"❌ Backup error: {e}")

def backup_scheduler():
    """Run backup every hour"""
    while True:
        time.sleep(3600)  # Wait 1 hour (3600 seconds)
        backup_to_mega()

# Start backup thread
backup_thread = threading.Thread(target=backup_scheduler, daemon=True)
backup_thread.start()
print("⏰ Hourly backup scheduler started")

# ============ API ENDPOINTS ============

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "status": "online",
        "service": "Prime X Hub Universal Chat",
        "database": "PostgreSQL Connected" if DATABASE_URL else "Not configured",
        "features": ["ranks", "persistent_storage", "moderation", "private_rooms", "user_tracking", "auto_backup"],
        "backup": "Mega.nz every 1 hour" if MEGA_EMAIL and MEGA_PASSWORD else "Not configured",
        "version": "3.1"
    })

@app.route('/send', methods=['POST'])
def send_message():
    """Endpoint to send a message - INSTANT NO DELAY"""
    try:
        data = request.get_json()
        
        if not data or 'username' not in data or 'message' not in data:
            return jsonify({"error": "Missing username or message"}), 400
        
        username = str(data['username'])[:50]
        message = str(data['message'])[:500]
        game = str(data.get('game', 'Unknown'))[:100]
        room = data.get('room')
        msg_type = data.get('type', 'general')
        
        # Skip heartbeat messages from being stored
        if msg_type == 'heartbeat':
            conn = get_db_connection()
            if conn:
                cur = conn.cursor()
                cur.execute('''
                    INSERT INTO active_users (username, game, last_seen)
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (username) 
                    DO UPDATE SET game = %s, last_seen = CURRENT_TIMESTAMP
                ''', (username, game, game))
                conn.commit()
                cur.close()
                conn.close()
            return jsonify({"success": True, "type": "heartbeat"}), 200
        
        # Check if user is banned (in-memory check first for speed)
        if username in BANNED_USERS:
            return jsonify({"error": "You are muted from the chat"}), 403
        
        # Get user rank
        rank_info = get_user_rank(username)
        
        # Save to database
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
            
        cur = conn.cursor()
        
        cur.execute(
            '''INSERT INTO messages (username, message, game, rank, rank_emoji, rank_color, room, message_type) 
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, timestamp''',
            (username, message, game, rank_info['rank'], rank_info['emoji'], rank_info['color'], room, msg_type)
        )
        
        result = cur.fetchone()
        
        # Update active users
        cur.execute('''
            INSERT INTO active_users (username, game, last_seen)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (username) 
            DO UPDATE SET game = %s, last_seen = CURRENT_TIMESTAMP
        ''', (username, game, game))
        
        conn.commit()
        cur.close()
        conn.close()
        
        return jsonify({
            "success": True,
            "data": {
                "id": result['id'],
                "username": username,
                "message": message,
                "rank": rank_info['rank'],
                "rank_emoji": rank_info['emoji'],
                "rank_color": rank_info['color'],
                "timestamp": result['timestamp'].isoformat()
            }
        }), 201
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/messages', methods=['GET'])
def get_messages():
    """Endpoint to get messages - INSTANT NO DELAY"""
    try:
        since_id = request.args.get('since_id', 0, type=int)
        limit = request.args.get('limit', 50, type=int)
        room = request.args.get('room')
        
        limit = min(limit, 100)
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
            
        cur = conn.cursor()
        
        if room:
            # Get room messages
            cur.execute(
                '''SELECT id, username, message, game, rank, rank_emoji, rank_color, timestamp 
                   FROM messages WHERE room = %s AND id > %s 
                   ORDER BY id ASC LIMIT %s''',
                (room, since_id, limit)
            )
        else:
            # Get general messages (no room)
            cur.execute(
                '''SELECT id, username, message, game, rank, rank_emoji, rank_color, timestamp 
                   FROM messages WHERE (room IS NULL OR room = '') AND id > %s 
                   ORDER BY id ASC LIMIT %s''',
                (since_id, limit)
            )
        
        messages = cur.fetchall()
        
        messages_list = []
        for msg in messages:
            messages_list.append({
                "id": msg['id'],
                "username": msg['username'],
                "message": msg['message'],
                "game": msg['game'],
                "rank": msg['rank'] or "Member",
                "rank_emoji": msg['rank_emoji'] or "👤",
                "rank_color": msg['rank_color'] or "#CCCCCC",
                "timestamp": msg['timestamp'].isoformat()
            })
        
        cur.close()
        conn.close()
        
        return jsonify({
            "success": True,
            "count": len(messages_list),
            "messages": messages_list
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/active-users', methods=['GET'])
def get_active_users():
    """Get list of currently active users"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
            
        cur = conn.cursor()
        
        cur.execute('''
            SELECT username, game, last_seen 
            FROM active_users 
            WHERE last_seen > %s
            ORDER BY last_seen DESC
        ''', (datetime.now() - timedelta(minutes=2),))
        
        users = cur.fetchall()
        
        active_list = []
        for user in users:
            active_list.append({
                "username": user['username'],
                "game": user['game'],
                "last_seen": user['last_seen'].isoformat()
            })
        
        cur.close()
        conn.close()
        
        return jsonify({
            "success": True,
            "count": len(active_list),
            "active_users": active_list
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/stats', methods=['GET'])
def get_stats():
    """Get chat statistics"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
            
        cur = conn.cursor()
        
        cur.execute('SELECT COUNT(*) as total FROM messages')
        total = cur.fetchone()['total']
        
        cur.execute('SELECT COUNT(DISTINCT username) as users FROM messages')
        users = cur.fetchone()['users']
        
        cur.execute('SELECT COUNT(*) as today FROM messages WHERE DATE(timestamp) = CURRENT_DATE')
        today = cur.fetchone()['today']
        
        cur.execute('SELECT COUNT(*) as banned FROM banned_users')
        banned = cur.fetchone()['banned']
        
        cur.execute('''
            SELECT COUNT(*) as active 
            FROM active_users 
            WHERE last_seen > %s
        ''', (datetime.now() - timedelta(minutes=2),))
        active = cur.fetchone()['active']
        
        cur.close()
        conn.close()
        
        return jsonify({
            "total_messages": total,
            "unique_users": users,
            "messages_today": today,
            "banned_users": banned,
            "active_users_now": active
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============ PRIVATE ROOMS ENDPOINTS ============

@app.route('/room/create', methods=['POST'])
def create_room():
    """Create a private room"""
    try:
        data = request.get_json()
        owner = data.get('owner')
        room_code = data.get('room_code')
        
        if not owner or not room_code:
            return jsonify({"error": "Missing owner or room_code"}), 400
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
            
        cur = conn.cursor()
        
        cur.execute('''
            INSERT INTO private_rooms (room_code, owner, is_private)
            VALUES (%s, %s, FALSE)
            ON CONFLICT (room_code) DO NOTHING
            RETURNING room_code
        ''', (room_code, owner))
        
        result = cur.fetchone()
        
        if not result:
            cur.close()
            conn.close()
            return jsonify({"error": "Room already exists"}), 400
        
        cur.execute('''
            INSERT INTO room_members (room_code, username)
            VALUES (%s, %s)
        ''', (room_code, owner))
        
        conn.commit()
        cur.close()
        conn.close()
        
        return jsonify({
            "success": True,
            "room_code": room_code,
            "owner": owner
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/room/join', methods=['POST'])
def join_room():
    """Join a private room"""
    try:
        data = request.get_json()
        username = data.get('username')
        room_code = data.get('room_code')
        
        if not username or not room_code:
            return jsonify({"error": "Missing username or room_code"}), 400
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
            
        cur = conn.cursor()
        
        cur.execute('''
            SELECT owner, is_private FROM private_rooms WHERE room_code = %s
        ''', (room_code,))
        
        room = cur.fetchone()
        
        if not room:
            cur.close()
            conn.close()
            return jsonify({"error": "Room not found"}), 404
        
        if room['is_private'] and room['owner'] != username:
            cur.close()
            conn.close()
            return jsonify({"error": "Room is private"}), 403
        
        cur.execute('''
            INSERT INTO room_members (room_code, username)
            VALUES (%s, %s)
            ON CONFLICT (room_code, username) DO NOTHING
        ''', (room_code, username))
        
        conn.commit()
        cur.close()
        conn.close()
        
        return jsonify({
            "success": True,
            "room_code": room_code,
            "username": username
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/room/toggle-privacy', methods=['POST'])
def toggle_room_privacy():
    """Toggle room privacy"""
    try:
        data = request.get_json()
        username = data.get('username')
        room_code = data.get('room_code')
        
       
