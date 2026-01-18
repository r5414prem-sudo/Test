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
import secrets
import hashlib

app = Flask(__name__)
CORS(app)

# 🔐 SECURITY: API Key Authentication
API_KEY = os.environ.get('API_KEY', secrets.token_urlsafe(32))  # Generate secure key
print(f"🔑 API Key: {API_KEY}")  # Show this ONCE when server starts

# 🔐 SECURITY: Trusted User IDs (use Roblox User IDs, not names!)
TRUSTED_USERS = {
    # Add your actual Roblox User IDs here
    # Example: 123456789: {"rank": "Owner", "emoji": "👑", "color": "#FFD700", "level": 3}
}

# 👑 RANK SYSTEM - BASED ON USER IDS (MORE SECURE)
RANKS_BY_USERID = {
    # REPLACE THESE WITH YOUR ACTUAL ROBLOX USER IDS
    # To find your ID: go to roblox.com/users/YOUR_PROFILE and look at the URL
    # Example: https://roblox.com/users/123456789/profile <- 123456789 is the ID
    
    # 123456789: {"username": "foffasfieifro", "rank": "Owner", "emoji": "👑", "color": "#FFD700", "level": 3},
    # 987654321: {"username": "Ya_shumi09", "rank": "Owner", "emoji": "👑", "color": "#FFD700", "level": 3},
    # 111222333: {"username": "shimul2222222", "rank": "Mod", "emoji": "🛡️", "color": "#4ECDC4", "level": 2},
}

DEFAULT_RANK = {"rank": "Member", "emoji": "👤", "color": "#CCCCCC", "level": 0}

BANNED_USERS = set()

# Database connection
DATABASE_URL = os.environ.get('DATABASE_URL')

# Mega.nz Configuration
MEGA_EMAIL = os.environ.get('MEGA_EMAIL')
MEGA_PASSWORD = os.environ.get('MEGA_PASSWORD')

def verify_api_key(request):
    """Verify API key from request"""
    api_key = request.headers.get('X-API-Key')
    return api_key == API_KEY

def get_user_rank(user_id):
    """Get rank info by User ID (secure)"""
    user_id = int(user_id) if user_id else 0
    return RANKS_BY_USERID.get(user_id, DEFAULT_RANK)

def is_staff(user_id):
    """Check if user is staff by User ID"""
    rank_info = get_user_rank(user_id)
    return rank_info.get('level', 0) >= 2

def is_owner(user_id):
    """Check if user is Owner by User ID"""
    rank_info = get_user_rank(user_id)
    return rank_info.get('level', 0) >= 3

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
    """Initialize database tables"""
    if not DATABASE_URL:
        print("⚠️  WARNING: DATABASE_URL not set")
        return False
    
    try:
        conn = get_db_connection()
        if not conn:
            return False
            
        cur = conn.cursor()
        
        # Messages table - now stores user_id
        cur.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
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
        
        # Banned users - by user_id
        cur.execute('''
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id BIGINT PRIMARY KEY,
                username VARCHAR(50),
                banned_by BIGINT,
                reason TEXT,
                banned_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Active users - by user_id
        cur.execute('''
            CREATE TABLE IF NOT EXISTS active_users (
                user_id BIGINT PRIMARY KEY,
                username VARCHAR(50),
                game VARCHAR(100),
                last_seen TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Private rooms
        cur.execute('''
            CREATE TABLE IF NOT EXISTS private_rooms (
                room_code VARCHAR(10) PRIMARY KEY,
                owner_id BIGINT NOT NULL,
                owner_name VARCHAR(50),
                is_private BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Room members
        cur.execute('''
            CREATE TABLE IF NOT EXISTS room_members (
                id SERIAL PRIMARY KEY,
                room_code VARCHAR(10) NOT NULL,
                user_id BIGINT NOT NULL,
                username VARCHAR(50),
                joined_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(room_code, user_id)
            )
        ''')
        
        cur.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp DESC)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_room ON messages(room)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_last_seen ON active_users(last_seen DESC)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_user_id ON messages(user_id)')
        
        conn.commit()
        cur.close()
        conn.close()
        print("✅ Database initialized successfully")
        return True
    except Exception as e:
        print(f"❌ Database initialization error: {e}")
        return False

# ============ MEGA.NZ BACKUP ============

def backup_to_mega():
    """Backup all chat logs to Mega.nz and clear database"""
    try:
        print("📦 Starting hourly backup to Mega.nz...")
        
        conn = get_db_connection()
        if not conn:
            return
        
        cur = conn.cursor()
        
        cur.execute('''
            SELECT id, user_id, username, message, game, rank, rank_emoji, 
                   rank_color, room, message_type, timestamp 
            FROM messages 
            ORDER BY timestamp ASC
        ''')
        
        messages = cur.fetchall()
        
        if len(messages) == 0:
            print("ℹ️  No messages to backup")
            cur.close()
            conn.close()
            return
        
        backup_data = {
            "backup_time": datetime.now().isoformat(),
            "message_count": len(messages),
            "messages": []
        }
        
        for msg in messages:
            backup_data["messages"].append({
                "id": msg['id'],
                "user_id": msg['user_id'],
                "username": msg['username'],
                "message": msg['message'],
                "game": msg['game'],
                "rank": msg['rank'],
                "timestamp": msg['timestamp'].isoformat()
            })
        
        filename = f"chat_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        with open(f"/tmp/{filename}", 'w') as f:
            json.dump(backup_data, f, indent=2)
        
        if MEGA_EMAIL and MEGA_PASSWORD:
            try:
                mega = Mega()
                m = mega.login(MEGA_EMAIL, MEGA_PASSWORD)
                
                folder = None
                files = m.get_files()
                
                for file_id in files:
                    file_data = files[file_id]
                    if file_data['a'] and file_data['a'].get('n') == 'PrimeX Chat Backups' and file_data['t'] == 1:
                        folder = file_data
                        break
                
                if not folder:
                    folder = m.create_folder('PrimeX Chat Backups')
                    print("📁 Created folder: PrimeX Chat Backups")
                
                file = m.upload(f"/tmp/{filename}", folder[0] if isinstance(folder, tuple) else folder)
                print(f"✅ Backup uploaded to Mega.nz/PrimeX Chat Backups/{filename}")
                
                os.remove(f"/tmp/{filename}")
                
            except Exception as e:
                print(f"❌ Mega.nz upload error: {e}")
        
        cur.execute('DELETE FROM messages')
        deleted = cur.rowcount
        
        conn.commit()
        cur.close()
        conn.close()
        
        print(f"✅ Backup complete! {deleted} messages cleared")
        
    except Exception as e:
        print(f"❌ Backup error: {e}")

def backup_scheduler():
    """Run backup every hour"""
    while True:
        time.sleep(3600)
        backup_to_mega()

backup_thread = threading.Thread(target=backup_scheduler, daemon=True)
backup_thread.start()
print("⏰ Hourly backup scheduler started")

# ============ SECURE API ENDPOINTS ============

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "status": "online",
        "service": "Prime X Hub Universal Chat - SECURED",
        "version": "4.0",
        "security": "API Key + User ID Authentication"
    })

@app.route('/send', methods=['POST'])
def send_message():
    """Send message - SECURED with API Key + User ID"""
    # Verify API key
    if not verify_api_key(request):
        return jsonify({"error": "Invalid API Key"}), 401
    
    try:
        data = request.get_json()
        
        if not data or 'user_id' not in data or 'username' not in data or 'message' not in data:
            return jsonify({"error": "Missing required fields"}), 400
        
        user_id = int(data['user_id'])
        username = str(data['username'])[:50]
        message = str(data['message'])[:500]
        game = str(data.get('game', 'Unknown'))[:100]
        room = data.get('room')
        msg_type = data.get('type', 'general')
        
        if msg_type == 'heartbeat':
            conn = get_db_connection()
            if conn:
                cur = conn.cursor()
                cur.execute('''
                    INSERT INTO active_users (user_id, username, game, last_seen)
                    VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (user_id) 
                    DO UPDATE SET username = %s, game = %s, last_seen = CURRENT_TIMESTAMP
                ''', (user_id, username, game, username, game))
                conn.commit()
                cur.close()
                conn.close()
            return jsonify({"success": True, "type": "heartbeat"}), 200
        
        # Check if user is banned
        if user_id in BANNED_USERS:
            return jsonify({"error": "You are muted"}), 403
        
        rank_info = get_user_rank(user_id)
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
            
        cur = conn.cursor()
        
        cur.execute(
            '''INSERT INTO messages (user_id, username, message, game, rank, rank_emoji, rank_color, room, message_type) 
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, timestamp''',
            (user_id, username, message, game, rank_info['rank'], rank_info['emoji'], rank_info['color'], room, msg_type)
        )
        
        result = cur.fetchone()
        
        cur.execute('''
            INSERT INTO active_users (user_id, username, game, last_seen)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id) 
            DO UPDATE SET username = %s, game = %s, last_seen = CURRENT_TIMESTAMP
        ''', (user_id, username, game, username, game))
        
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
    """Get messages - SECURED"""
    if not verify_api_key(request):
        return jsonify({"error": "Invalid API Key"}), 401
    
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
            cur.execute(
                '''SELECT id, username, message, game, rank, rank_emoji, rank_color, timestamp 
                   FROM messages WHERE room = %s AND id > %s 
                   ORDER BY id ASC LIMIT %s''',
                (room, since_id, limit)
            )
        else:
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

@app.route('/mute', methods=['POST'])
def mute_user():
    """Mute user - SECURED"""
    if not verify_api_key(request):
        return jsonify({"error": "Invalid API Key"}), 401
    
    try:
        data = request.get_json()
        moderator_id = int(data.get('moderator_id'))
        target_user_id = int(data.get('target_user_id'))
        target_username = data.get('target_username', 'Unknown')
        reason = data.get('reason', 'No reason')
        
        if not is_staff(moderator_id):
            return jsonify({"error": "Unauthorized"}), 403
        
        if is_staff(target_user_id):
            return jsonify({"error": "Cannot mute staff"}), 400
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
            
        cur = conn.cursor()
        
        cur.execute('''
            INSERT INTO banned_users (user_id, username, banned_by, reason) 
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO NOTHING
        ''', (target_user_id, target_username, moderator_id, reason))
        
        conn.commit()
        cur.close()
        conn.close()
        
        BANNED_USERS.add(target_user_id)
        
        return jsonify({"success": True, "message": f"{target_username} muted"})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/unmute', methods=['POST'])
def unmute_user():
    """Unmute user - SECURED"""
    if not verify_api_key(request):
        return jsonify({"error": "Invalid API Key"}), 401
    
    try:
        data = request.get_json()
        moderator_id = int(data.get('moderator_id'))
        target_user_id = int(data.get('target_user_id'))
        
        if not is_staff(moderator_id):
            return jsonify({"error": "Unauthorized"}), 403
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
            
        cur = conn.cursor()
        cur.execute('DELETE FROM banned_users WHERE user_id = %s', (target_user_id,))
        conn.commit()
        cur.close()
        conn.close()
        
        BANNED_USERS.discard(target_user_id)
        
        return jsonify({"success": True})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/clear', methods=['POST'])
def clear_messages():
    """Clear messages - SECURED"""
    if not verify_api_key(request):
        return jsonify({"error": "Invalid API Key"}), 401
    
    try:
        data = request.get_json()
        user_id = int(data.get('user_id', 0))
        
        if not is_staff(user_id):
            return jsonify({"error": "Unauthorized"}), 403
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
            
        cur = conn.cursor()
        cur.execute('DELETE FROM messages')
        deleted = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        
        return jsonify({"success": True, "deleted": deleted})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/stats', methods=['GET'])
def get_stats():
    """Get chat statistics"""
    if not verify_api_key(request):
        return jsonify({"error": "Invalid API Key"}), 401
    
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
            
        cur = conn.cursor()
        
        cur.execute('SELECT COUNT(*) as total FROM messages')
        total = cur.fetchone()['total']
        
        cur.execute('SELECT COUNT(DISTINCT user_id) as users FROM messages')
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

@app.route('/manual-backup', methods=['POST'])
def manual_backup():
    """Manually trigger backup (Owner only)"""
    if not verify_api_key(request):
        return jsonify({"error": "Invalid API Key"}), 401
    
    try:
        data = request.get_json()
        user_id = int(data.get('user_id', 0))
        
        if not is_owner(user_id):
            return jsonify({"error": "Unauthorized - Owners only"}), 403
        
        threading.Thread(target=backup_to_mega, daemon=True).start()
        
        return jsonify({
            "success": True,
            "message": "Backup started"
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    print("🚀 Starting SECURE Prime X Chat Server v4.0...")
    print("="*60)
    
    if DATABASE_URL:
        if init_database():
            print("✅ Database ready!")
    else:
        print("⚠️  WARNING: DATABASE_URL not set!")
    
    if MEGA_EMAIL and MEGA_PASSWORD:
        print("☁️  Mega.nz backup configured")
    else:
        print("⚠️  Mega.nz not configured")
    
  
