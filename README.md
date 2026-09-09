## ขั้นตอนการ Set up

### 1. ติดตั้ง Dependencies
```bash
pip install -r requirements.txt
pip install Authlib
```

### 2. ตั้งค่า Google OAuth 2.0
1. ไปที่ [Google Cloud Console](https://console.cloud.google.com/)
2. สร้างโปรเจกต์ใหม่หรือเลือกโปรเจกต์ที่มีอยู่
3. เปิดใช้งาน Google+ API
4. สร้าง OAuth 2.0 Client ID:
   - Authorized JavaScript origins: `http://localhost:5000`
   - Authorized redirect URIs: `http://localhost:5000/auth/callback`
5. คัดลอก Client ID และ Client Secret

### 3. ตั้งค่า Environment Variables
แก้ไขไฟล์ `.env`:
```env
GOOGLE_CLIENT_ID=your_google_client_id_here
GOOGLE_CLIENT_SECRET=your_google_client_secret_here
MYSQL_HOST=localhost
MYSQL_USER=root
MYSQL_PASSWORD=your_mysql_password
MYSQL_DB=it_career_advisor
SECRET_KEY=your_secret_key_here
```

### 4. ตั้งค่า MySQL Database
1. ติดตั้งและรัน MySQL Server
2. สร้างฐานข้อมูล:
```bash
python setup_db.py
```

### 5. รัน Ollama Model
```bash
ollama run qwen2.5:1.5b
```
รอจนรันเสร็จแล้วพิมพ์ `/bye`

### 6. รันแอปพลิเคชัน
```bash
python app.py
```

### 7. เข้าถึงแอปพลิเคชัน
เปิดเบราว์เซอร์ไปที่ `http://localhost:5000`

---

## โมเดลที่รองรับ
- llama3.1
- qwen2.5:7b
- qwen2.5:1.5b

## คุณสมบัติ
- การเข้าสู่ระบบด้วย Google OAuth 2.0
- การเก็บประวัติการแชทในฐานข้อมูล MySQL
- RAG system สำหรับคำแนะนำอาชีพ IT
- UI ที่เป็นมิตรกับผู้ใช้