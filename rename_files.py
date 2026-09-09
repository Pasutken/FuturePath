import os
import glob

# กำหนดโฟลเดอร์เป้าหมาย
base_dir = "knowledge_base"
kb_folders = ["careers", "education", "faqs_and_advice"]

count = 0
print("🔍 กำลังค้นหาและเปลี่ยนชื่อไฟล์...")

for folder in kb_folders:
    folder_path = os.path.join(base_dir, folder)
    if os.path.exists(folder_path):
        # หาไฟล์ .md ทั้งหมดในโฟลเดอร์
        md_files = glob.glob(os.path.join(folder_path, "*.md"))
        
        for file_path in md_files:
            filename = os.path.basename(file_path)
            
            # ตรวจสอบว่ามีเครื่องหมาย _ หรือไม่
            if '_' in filename:
                # แทนที่ _ ด้วยช่องว่าง
                new_filename = filename.replace('_', ' ')
                new_file_path = os.path.join(folder_path, new_filename)
                
                # ทำการเปลี่ยนชื่อไฟล์ (Rename)
                os.rename(file_path, new_file_path)
                print(f"✅ เปลี่ยนชื่อ: '{filename}' ➡️ '{new_filename}'")
                count += 1

if count == 0:
    print("✨ ไม่มีไฟล์ที่ต้องเปลี่ยนชื่อ (ไฟล์ทั้งหมดถูกตั้งชื่ออย่างถูกต้องแล้ว)")
else:
    print(f"\n🎉 ดำเนินการสำเร็จ! เปลี่ยนชื่อไฟล์ไปทั้งหมด {count} ไฟล์")