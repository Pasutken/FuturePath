import os
from flask import Flask, render_template, request, jsonify, redirect, url_for, make_response
from authlib.integrations.flask_client import OAuth
from authlib.integrations.base_client import OAuthError
import mysql.connector
from mysql.connector import Error
from functools import wraps
from datetime import timedelta
from main import get_ai_response
import secrets
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Flask configuration
app.secret_key = os.getenv('SECRET_KEY', secrets.token_hex(32))
app.config['SESSION_COOKIE_SECURE'] = False  
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

# OAuth configuration
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'openid email profile',
    }
)

def get_db_connection():
    try:
        connection = mysql.connector.connect(
            host=os.getenv('MYSQL_HOST', 'localhost'),
            user=os.getenv('MYSQL_USER', 'root'),
            password=os.getenv('MYSQL_PASSWORD', ''),
            port=int(os.getenv('MYSQL_PORT', 3307)),
            database=os.getenv('MYSQL_DB', 'it_career_advisor')
        )
        return connection
    except Error as e:
        print(f"Error connecting to MySQL: {e}")
        return None

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user_id = request.cookies.get('user_id')
        if not user_id:
            return redirect(url_for('login'))
        
        connection = get_db_connection()
        if connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("SELECT id FROM users WHERE id = %s", (user_id,))
            user = cursor.fetchone()
            cursor.close()
            connection.close()
            if not user:
                return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def get_current_user():
    user_id = request.cookies.get('user_id')
    if user_id:
        connection = get_db_connection()
        if connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            user = cursor.fetchone()
            cursor.close()
            connection.close()
            return user
    return None

def save_chat_history(user_id, session_id, question, answer):
    connection = get_db_connection()
    if connection:
        cursor = connection.cursor()
        cursor.execute(
            "INSERT INTO chat_history (user_id, session_id, question, answer) VALUES (%s, %s, %s, %s)",
            (user_id, session_id, question, answer)
        )
        connection.commit()
        cursor.close()
        connection.close()

@app.route('/')
@login_required
def index():
    user = get_current_user()
    return render_template('index.html', user=user)

@app.route('/login')
def login():
    return render_template('login.html')

@app.route('/logout')
def logout():
    response = make_response(redirect(url_for('login')))
    response.set_cookie('user_id', '', expires=0)
    return response

@app.route('/auth/google')
def google_login():
    redirect_uri = url_for('google_callback', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/auth/callback')
def google_callback():
    try:
        token = google.authorize_access_token()
        user_info = token.get('userinfo') or google.get('https://www.googleapis.com/oauth2/v1/userinfo').json()
        google_id = user_info.get('sub') or user_info.get('id')

        connection = get_db_connection()
        if connection:
            cursor = connection.cursor()
            cursor.execute(
                "INSERT INTO users (google_id, email, name, picture) VALUES (%s, %s, %s, %s) ON DUPLICATE KEY UPDATE name=%s, picture=%s",
                (google_id, user_info['email'], user_info['name'], user_info.get('picture', ''), user_info['name'], user_info.get('picture', ''))
            )
            connection.commit()
            
            cursor.execute("SELECT id FROM users WHERE google_id = %s", (google_id,))
            user = cursor.fetchone()
            cursor.close()
            connection.close()

            if user:
                response = make_response(redirect(url_for('index')))
                response.set_cookie('user_id', str(user[0]), max_age=60*60*24*7) 
                return response

    except OAuthError as e:
        print(f"OAuth error: {e}")
        return redirect(url_for('login'))
    return redirect(url_for('login'))

@app.route('/api/ask', methods=['POST'])
@login_required
def ask():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_input = request.json.get('question')
    session_id = request.json.get('session_id')
    
    if not user_input or not session_id:
        return jsonify({'answer': "ข้อมูลไม่ครบถ้วน กรุณาใส่คำถามและ Session ID"}), 400

    # ดึงประวัติแชทเก่าเฉพาะใน Session นี้ (ดึง 3 คำถามล่าสุดจริงๆ)
    connection = get_db_connection()
    history_messages = []
    if connection:
        cursor = connection.cursor(dictionary=True)
        # 1. เปลี่ยนเป็น DESC เพื่อดึงข้อความใหม่ล่าสุดขึ้นมาก่อน
        cursor.execute(
            "SELECT question, answer FROM chat_history WHERE user_id = %s AND session_id = %s ORDER BY timestamp DESC LIMIT 3",
            (user['id'], session_id)
        )
        old_chats = cursor.fetchall()
        
        # 2. สลับลำดับ (Reverse) ให้กลับมาเรียงจาก อดีต -> ปัจจุบัน ตามธรรมชาติการสนทนา
        old_chats.reverse() 
        
        for chat in old_chats:
            history_messages.append({"role": "user", "content": chat['question']})
            history_messages.append({"role": "assistant", "content": chat['answer']})
        cursor.close()
        connection.close()

    # Get AI response
    answer = get_ai_response(user_input, history_messages)
    
    # Save to chat history
    save_chat_history(user['id'], session_id, user_input, answer)
    return jsonify({'answer': answer})

@app.route('/api/chat-history')
@login_required
def get_chat_history():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Unauthorized'}), 401

    connection = get_db_connection()
    if connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT session_id, question, answer, timestamp FROM chat_history WHERE user_id = %s ORDER BY timestamp DESC LIMIT 50",
            (user['id'],)
        )
        history = cursor.fetchall()
        cursor.close()
        connection.close()

        for item in history:
            if item['timestamp']:
                item['timestamp'] = item['timestamp'].strftime("%Y-%m-%d %H:%M:%S")

        return jsonify({'history': history})
    return jsonify({'history': []})

@app.route('/api/chat-session/<session_id>', methods=['DELETE'])
@login_required
def delete_chat_session(session_id):
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Unauthorized'}), 401

    connection = get_db_connection()
    if connection:
        cursor = connection.cursor()
        # ลบข้อความทั้งหมดที่ตรงกับ user_id และ session_id นั้นๆ
        cursor.execute(
            "DELETE FROM chat_history WHERE user_id = %s AND session_id = %s",
            (user['id'], session_id)
        )
        connection.commit()
        cursor.close()
        connection.close()
        return jsonify({'success': True})
        
    return jsonify({'error': 'Database connection failed'}), 500

if __name__ == '__main__':
    app.run(host='localhost', port=5000, debug=True)