import pandas as pd
from sentence_transformers import SentenceTransformer
import faiss
import numpy as np
import PyPDF2  
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import os
import glob
import re
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(
    api_key=os.getenv("API_KEY"),
    base_url=os.getenv("BASE_URL")
)

# ==========================================
# FastAPI Setup: ตั้งค่าพื้นฐานสำหรับ Web API
# ทำหน้าที่เปิดประตูให้หน้าเว็บ (Frontend) สามารถส่งคำถามเข้ามาคุยกับ Python (Backend) ได้
# ==========================================
app = FastAPI(title="IT Job Market RAG API (Optimized with PDF Support & Memory)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")

# กำหนดโครงสร้างข้อมูลที่รับเข้าและส่งออก
class QueryRequest(BaseModel):
    question: str

class SourceItem(BaseModel):
    source_type: str
    title: str
    detail: str

class QueryResponse(BaseModel):
    question: str
    expanded_query: str 
    answer: str
    sources: list[SourceItem]

print("--- Initializing Optimized RAG System ---")

# ==========================================
# 0. ฟังก์ชันสำหรับเตรียมข้อมูล (Advanced Text Preparation & Chunking)
# 🧹 หน้าที่: "พ่อครัวเตรียมวัตถุดิบ" 
# คอยทำความสะอาดข้อความ (ลบอักขระแปลกๆ, สัญลักษณ์) และ "สับ" ไฟล์เอกสารยาวๆ (PDF, Markdown) 
# ให้กลายเป็นชิ้นเล็กๆ (Chunks) เพื่อให้ AI อ่านและค้นหาได้ง่ายขึ้นโดยไม่เกิดอาการความจำล้น
# ==========================================
def clean_text_for_embedding(text):
    """ทำความสะอาด Markdown พื้นฐาน"""
    text = re.sub(r'#+\s*', '', text) 
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text) 
    text = re.sub(r'\*(.*?)\*', r'\1', text) 
    text = re.sub(r'\s+', ' ', text) 
    return text.strip()

def clean_pdf_text(text):
    """ทำความสะอาดข้อความที่ดึงมาจาก PDF (ลบเลขหน้า, ลบคำซ้ำ, จัดช่องว่าง)"""
    # 1. ลบเลขหน้า
    text = re.sub(r'-\s*\d+\s*-', '', text)
    # 2. ลบข้อความหัว/ท้ายกระดาษที่ปรากฏซ้ำๆ
    text = text.replace('ทิศทางตลาดแรงงานไทยในอนาคต : อุตสาหกรรมชิ้นส่วนอิเล็กทรอนิกส์', '')
    text = text.replace('บทที่ 4', '')
    text = text.replace('ผลการศึกษา', '')
    # 3. ลบสัญลักษณ์ Bullet และขยะอื่นๆ
    text = re.sub(r'[•◦○O]', '', text)
    # 4. จัดการประโยคที่ถูกตัดฉีกขาด
    text = text.replace('\n', ' ')
    # 5. จัดระเบียบช่องว่าง
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def extract_and_chunk_markdown(file_path, filename):
    """อ่านไฟล์ .md และหั่นเป็นชิ้น (Chunk) ตามย่อหน้า พร้อมแปะป้ายชื่อไฟล์กำกับไว้ทุกชิ้น"""
    text_chunks = []
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            content = file.read()
            paragraphs = content.split('\n\n')
            for p in paragraphs:
                cleaned_p = clean_text_for_embedding(p)
                if len(cleaned_p) > 30: 
                    contextualized_chunk = f"[อ้างอิงจากหัวข้อ: {filename}] {cleaned_p}"
                    text_chunks.append(contextualized_chunk)
    except Exception as e:
        print(f"เกิดข้อผิดพลาดในการอ่านไฟล์ Markdown ({file_path}): {e}")
    return text_chunks

def extract_and_chunk_pdf(pdf_path, title):
    """อ่านไฟล์ .pdf และหั่นเป็นชิ้น (Chunk) แบบจำกัดตัวอักษรและมีส่วนซ้อนทับกัน (Overlap)"""
    text_chunks = []
    try:
        with open(pdf_path, 'rb') as file:
            reader = PyPDF2.PdfReader(file)
            full_text = ""
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    full_text += page_text + " "
            
            cleaned_full_text = clean_pdf_text(full_text)
            
            # แบ่ง Chunk 800 ตัวอักษร ซ้อนทับกัน 100 ตัวอักษร (กันข้อมูลขาดตอน)
            chunk_size = 800  
            overlap = 100     
            
            for i in range(0, len(cleaned_full_text), chunk_size - overlap):
                chunk_text = cleaned_full_text[i:i + chunk_size]
                if len(chunk_text) > 30:
                    contextualized_chunk = f"[อ้างอิงจาก PDF: {title}] {chunk_text}"
                    text_chunks.append(contextualized_chunk)
    except Exception as e:
        print(f"เกิดข้อผิดพลาดในการอ่าน PDF ({pdf_path}): {e}")
    return text_chunks


# ==========================================
# 1. เตรียมข้อมูลรวม (CSV + Knowledge Base + PDF)
# 🗂️ หน้าที่: "บรรณารักษ์รวบรวมหนังสือ"
# คอยวิ่งไปอ่านไฟล์ทั้งหมดที่มีในระบบ (CSV ประกาศงาน, ไฟล์ MD ความรู้, ไฟล์ PDF สถิติ) 
# นำมาผ่านฟังก์ชันพ่อครัว (Section 0) แล้วเก็บรวบรวมไว้ใน List (`dataset_texts`) เพื่อรอเอาไปทำดัชนีค้นหา
# ==========================================
print("Loading data from existing CSV, Knowledge Base, and PDFs...")
dataset_texts = []
metadata = [] 

try:
    if os.path.exists('tech_jobs_cleaned_E.csv'):
        df = pd.read_csv('tech_jobs_cleaned_E.csv')
        df = df.fillna("") 
        for _, row in df.iterrows():
            text = (f"[ข้อมูลตำแหน่งงานจาก CSV] ตำแหน่ง: {row['Job_Title']}, "
                    f"หมวดหมู่: {row['Category']}, "
                    f"ทักษะที่ต้องการ (Skills): {row['Skills_Required']}, "
                    f"วุฒิการศึกษา: {row['Degree']}, "
                    f"สถานที่: {row['Location']}, รูปแบบการทำงาน: {row['Work_Model']}, "
                    f"เงินเดือน: {row['Salary_THB']} บาท, ประสบการณ์: {row['Years_Experience']} ปี")
            dataset_texts.append(clean_text_for_embedding(text))
            metadata.append({
                "source": "csv",
                "title": row['Job_Title'],
                "location": row['Location'],
                "salary": row['Salary_THB']
            })
        print(f"✓ Loaded {len(df)} records from CSV")
    else:
        print("⚠️ ไม่พบไฟล์ CSV")
except Exception as e:
    print(f"❌ ไม่สามารถโหลดไฟล์ CSV ได้: {e}")

base_dir = "knowledge_base"
kb_folders = ["careers", "education", "faqs_and_advice"]
total_md_chunks = 0
for folder in kb_folders:
    folder_path = os.path.join(base_dir, folder)
    if os.path.exists(folder_path):
        md_files = glob.glob(os.path.join(folder_path, "*.md"))
        for file_path in md_files:
            filename = os.path.basename(file_path).replace('.md', '')
            chunks = extract_and_chunk_markdown(file_path, filename)
            total_md_chunks += len(chunks)
            for chunk in chunks:
                dataset_texts.append(chunk)
                metadata.append({"source": folder, "title": filename, "location": "-", "salary": "-"})
print(f"✓ Loaded {total_md_chunks} chunks from Knowledge Base folders")

pdf_files = [
    {
        "path": "แนวโน้มตลาดแรงงานสาย IT ในปี 2025.pdf", 
        "title": "บทความแนวโน้มตลาดแรงงาน IT 2025",
    },
    {
        "path": "ทิศทางตลาดแรงงานไทยในอนาคต_อุตสาหกรรมชิ้นส่วนอิเล็กทรอนิกส_organized.pdf",
        "title": "รายงานทิศทางตลาดแรงงานอิเล็กทรอนิกส์"
    }
]

total_pdf_chunks = 0
for pdf in pdf_files:
    if os.path.exists(pdf['path']):
        print(f"Extracting PDF data from {pdf['path']}...")
        pdf_chunks = extract_and_chunk_pdf(pdf['path'], pdf['title'])
        total_pdf_chunks += len(pdf_chunks)
        for chunk in pdf_chunks:
            dataset_texts.append(chunk)
            metadata.append({"source": "pdf", "title": pdf['title'], "location": "-", "salary": "-"})
    else:
        print(f"⚠️ ไม่พบไฟล์ PDF: {pdf['path']}")
print(f"✓ Loaded {total_pdf_chunks} chunks from PDFs")


# ==========================================
# 2. ส่วนของ Retrieval (Sentence Transformers + FAISS)
# 🧠 หน้าที่: "สมองส่วนความจำและค้นหา" (Vector Database)
# แปลงข้อความตัวหนังสือทั้งหมดให้เป็น "ตัวเลข (Vector/Embedding)" และเก็บลงฐานข้อมูล FAISS
# เมื่อมีคำถามเข้ามา จะแปลงคำถามเป็นตัวเลขเช่นกัน แล้ววัดระยะทางคณิตศาสตร์เพื่อหาข้อมูลที่ "ความหมายใกล้เคียงที่สุด"
# ==========================================
print("Loading embedding model...")
embedding_model = SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')

print("Creating dataset embeddings...")
if not dataset_texts:
    dataset_texts = ["ระบบยังไม่มีข้อมูล"]
    metadata = [{"source": "system", "title": "Empty", "location": "-", "salary": "-"}]
    
# แปลงข้อความทั้งหมดเป็น Vector และสร้าง FAISS Index
embeddings = embedding_model.encode(dataset_texts, convert_to_numpy=True)
dimension = embeddings.shape[1]
index = faiss.IndexFlatL2(dimension)
index.add(embeddings.astype('float32'))

def retrieve_relevant_documents(search_query, k=5):
    """รับคำถามเข้ามา แปลงเป็น Vector แล้วดึงข้อมูลที่เกี่ยวข้องที่สุดออกมา k ชิ้น"""
    question_embedding = embedding_model.encode([search_query], convert_to_numpy=True)
    distances, indices = index.search(question_embedding.astype('float32'), k)
    retrieved_docs = []
    for idx in indices[0]:
        if idx < len(dataset_texts):
            retrieved_docs.append({'text': dataset_texts[idx], 'metadata': metadata[idx]})
    return retrieved_docs

def create_context(retrieved_docs):
    """นำข้อมูลที่ค้นหาเจอมามัดรวมกันเป็นก้อนเดียว (String) เพื่อเตรียมส่งให้ AI อ่าน"""
    context = "ข้อมูลที่เกี่ยวข้องจากฐานข้อมูลและเอกสารอ้างอิง:\n"
    for i, doc in enumerate(retrieved_docs, 1):
        context += f"\n[{i}]. {doc['text']}"
    return context

def preprocess_query(original_question):
    """ทำความสะอาดคำถามของผู้ใช้ก่อนนำไปค้นหา"""
    cleaned = re.sub(r'[^\w\sก-๙]', '', original_question) 
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


# ==========================================
# 4. ส่วนของ Generation (สร้างคำตอบ + Memory + Query Reformulation)
# 🤖 หน้าที่: "ปากและสมองส่วนคิดวิเคราะห์" (LLM Generation)
# รับข้อมูล Context ที่หาเจอ (จากข้อ 2) + ประวัติแชท + กฎเกณฑ์ต่างๆ (System Prompt) 
# ส่งไปให้โมเดล AI (Qwen/Local LLM) สรุปและตอบคำถามกลับมาให้ผู้ใช้
# ==========================================
print("Ready to use local Model...")

def generate_rag_answer(question, context, history_messages=[]):
    """แกนหลักในการตอบคำถาม: ประกอบร่าง Prompt ส่งให้ AI ตอบ"""
    messages = [
        {
            "role": "system", 
            "content": """คุณคือ "IT Career Advisor" แชทบอทผู้เชี่ยวชาญด้านการแนะนำเส้นทางการศึกษาต่อและสายอาชีพ IT หน้าที่หลักของคุณคือการให้คำปรึกษาเพื่อจับคู่อาชีพในสายเทคโนโลยีกับคณะหรือสาขาวิชาที่ควรเรียนอย่างแม่นยำและตรงไปตรงมา

[1. กฎการคัดกรองข้อมูล (Strict Data Filtering)]
- หากรายการใดมีข้อมูลที่ผู้ใช้ถามหาเป็น "ไม่ระบุ", "ไม่มีข้อมูล" หรือ "N/A" ให้ตัดรายการนั้นทิ้งทั้งบรรทัด ห้ามนำมาแสดงผลเด็ดขาด

[2. กฎการจัดการคำถามที่ตอบไม่ได้ (Zero Hallucination & Scope Control)]
- ตอบคำถามโดยอ้างอิงจากข้อมูลใน <context> เท่านั้น ห้ามเดาหรือแต่งข้อมูล ตัวเลขเงินเดือน หรือสถิติขึ้นมาเองเด็ดขาด
- กรณีไม่มีข้อมูลในระบบ (Missing Data): หากคำถามเกี่ยวข้องกับสายอาชีพ IT หรือการศึกษา แต่ไม่มีข้อมูลใน <context> ให้ตอบว่า "ขออภัยครับ ในฐานข้อมูลตอนนี้ยังไม่มีรายละเอียดเกี่ยวกับเรื่องนี้ แต่คุณสามารถสอบถามเกี่ยวกับสายงาน IT อื่นๆ หรือสาขาวิชาที่เกี่ยวข้องเพิ่มเติมได้นะครับ"
- กรณีนอกขอบเขต (Out of Scope): หากคำถามไม่เกี่ยวกับ IT ให้ตอบว่า "ขออภัยครับ ผมเป็นแชทบอทที่เน้นให้คำแนะนำด้านสายอาชีพ IT และเส้นทางการศึกษาเท่านั้นครับ หากมีข้อสงสัยเกี่ยวกับอาชีพสายเทคฯ ลองถามมาได้เลยครับ"
- ข้อจำกัดการทำงาน: ระบบนี้มุ่งเน้นไปที่การให้ข้อมูลเพื่อการศึกษาต่อ (Career-to-Major) "ไม่มี" ฟังก์ชันในการตรวจวิเคราะห์ Portfolio หรือการวาด Roadmap การทำงานแบบเจาะจงบุคคล หากผู้ใช้ขอให้ทำสิ่งเหล่านี้ ให้ปฏิเสธอย่างสุภาพและอธิบายจุดประสงค์หลักของระบบ

[3. โครงสร้างและรูปแบบการตอบ (Formatting)]
- สามารถอ้างอิงเนื้อหาจากการสนทนาก่อนหน้า (History) เพื่อความต่อเนื่องได้
- เริ่มต้นตอบคำถามทันที เข้าประเด็นเลย ห้ามมีประโยคเกริ่นนำน่าเบื่อ
- หากเป็นการเปรียบเทียบข้อมูลเชิงตัวเลข ให้ใช้ตาราง (Markdown Table) เสมอ
- หากเป็นคำแนะนำ ให้ใช้ Bullet points (-) 
- ใช้ **ตัวหนา** เพื่อเน้น Keyword สำคัญ"""
        }
    ]

    # แทรกประวัติแชทเพื่อให้ AI จำบริบทได้ (จำกัดแค่ 6 ข้อความล่าสุดเพื่อไม่ให้ล้น Token)
    for msg in history_messages[-6:]:
        messages.append(msg)

    # แทรกข้อมูล Context จาก FAISS และคำถามล่าสุด
    messages.append({
        "role": "user", 
        "content": f"<context>\n{context}\n</context>\n\nคำถาม: {question}"
    })

    try:
        response = client.chat.completions.create(
            model="gpt-oss:20b",
            # model="openai/gpt-oss-120b:free",
            messages=messages,
            temperature=0.15,
            top_p=0.7
        )
        return response.choices[0].message.content.strip()
        
    except Exception as e:
        print(f"เกิดข้อผิดพลาดในการเชื่อมต่อ AI API: {e}")
        return "ขออภัย ไม่สามารถเชื่อมต่อกับ AI Provider ได้ในขณะนี้"


def reformulate_query(current_question, history_messages):
    """ตัวช่วยแก้ปัญหาความจำเสื่อม (Query Reformulator)
    หน้าที่: นำคำถามสั้นๆ ของผู้ใช้ มาเติมประธานหรือบริบทจากประวัติแชทให้สมบูรณ์ ก่อนนำไปค้นหาใน FAISS"""
    if not history_messages:
        return current_question
        
    history_text = ""
    for msg in history_messages[-4:]:
        role = "ผู้ใช้" if msg['role'] == 'user' else "AI"
        content = msg['content'][:100] + "..." if len(msg['content']) > 100 else msg['content']
        history_text += f"{role}: {content}\n"
        
    prompt = f"""คุณคือ AI ผู้ช่วยปรับแต่งคำค้นหา (Query Reformulator)
หน้าที่ของคุณคือวิเคราะห์ "ประวัติแชท" และนำ "คำถามล่าสุด" มาเขียนใหม่ให้สมบูรณ์เพื่อนำไปค้นหาในฐานข้อมูล

กฎเหล็ก (Strict Rules):
1. หากคำถามล่าสุดเป็นเพียง "คำสั้นๆ" หรือ "ชื่ออาชีพ" (เช่น "Data Analyst", "Python") ห้ามแต่งเป็นประโยคคำถาม ให้ตอบกลับด้วยคำนั้นๆ เลย
2. หากคำถามล่าสุดละประธานไว้ (เช่น "เงินเดือนเท่าไหร่", "ทำอะไรบ้าง") ให้ดึง "ชื่ออาชีพ" จากประวัติแชทมาเติม 
3. **สำคัญมาก:** หากในประวัติแชท "ไม่มีชื่ออาชีพหรือบริบทที่ชัดเจน" ห้ามเดาเอง และห้ามเติมคำว่า "สายไอที" ให้คงคำถามเดิมไว้
4. ห้ามมีคำอธิบาย ตอบกลับแค่ข้อความที่พร้อมค้นหาเท่านั้น

ตัวอย่างที่ 1 (เติมประธานจากประวัติ)
ประวัติแชท:
ผู้ใช้: สนใจอาชีพ Front-end
AI: Front-end คือนักพัฒนา...
คำถามล่าสุด: เงินเดือนเท่าไหร่
คำถามที่เขียนใหม่: อาชีพ Front-end เงินเดือนเท่าไหร่

ตัวอย่างที่ 2 (พิมพ์แค่คำสั้นๆ - ห้ามแต่งเติม)
ประวัติแชท:
ผู้ใช้: สวัสดี
AI: สวัสดีครับ มีอะไรให้ช่วยไหม
คำถามล่าสุด: Data Analyst
คำถามที่เขียนใหม่: Data Analyst

ตัวอย่างที่ 3 (ไม่มีประวัติที่ชัดเจน - ห้ามมโน)
ประวัติแชท:
ผู้ใช้: อยากหางานทำ
AI: คุณสนใจด้านไหนครับ
คำถามล่าสุด: เงินเดือนเท่าไหร่
คำถามที่เขียนใหม่: เงินเดือนเท่าไหร่

---
ประวัติแชทจริง:
{history_text}
คำถามล่าสุด: {current_question}
คำถามที่เขียนใหม่:"""

    try:
        response = client.chat.completions.create(
            model="gpt-oss:20b",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0 # บังคับให้เป็น 0.0 เสมอเพื่อความแม่นยำสูงสุด
        )
        reformulated = response.choices[0].message.content.strip()
        return reformulated.strip('"\'') 
    except Exception as e:
        print(f"Reformulate Error: {e}")
        return current_question
    
# ==========================================
# 5. ฟังก์ชันสำหรับให้ app.py เรียกใช้งาน (Main Pipeline / Interface)
# 🔌 หน้าที่: "ท่อร้อยสายไฟ" 
# นำกระบวนการทั้งหมดมาต่อกันเป็น Workflow เดียว เพื่อให้ไฟล์อื่นๆ (เช่นหน้าบ้าน) เรียกใช้งานได้ง่ายๆ ด้วยบรรทัดเดียว
# ขั้นตอน: รับคำถาม -> เกลาคำถาม -> ค้นหาใน FAISS -> ส่งให้ AI สร้างคำตอบ -> คืนค่าคำตอบ
# ==========================================
def get_ai_response(question, history_messages=[]):
    # 1. ให้ AI เขียนคำถามใหม่ให้สมบูรณ์ก่อน
    search_query = reformulate_query(question, history_messages)
    print(f"\n[Original Query]: {question}")
    print(f"[Reformulated Query]: {search_query}")
    
    # 2. นำคำถามที่สมบูรณ์แล้วไปทำ Preprocess และค้นหาใน FAISS
    processed_query = preprocess_query(search_query)
    retrieved_docs = retrieve_relevant_documents(processed_query, k=15)
    
    if not retrieved_docs:
        context = "ไม่พบข้อมูลที่เกี่ยวข้องในฐานข้อมูล"
    else:
        context = create_context(retrieved_docs)

    # 3. Generate คำตอบ (เวลาตอบ ให้ตอบอิงจากคำถามดั้งเดิมของผู้ใช้)
    answer = generate_rag_answer(question, context, history_messages)
    
    return answer

# ==========================================
# 6. คำสั่งรัน Server สำหรับทดสอบ FastAPI 
# 🚀 หน้าที่: สั่งเปิดเซิร์ฟเวอร์เพื่อให้ระบบออนไลน์ (ทำงานเมื่อรันไฟล์นี้โดยตรง)
# ==========================================
if __name__ == "__main__":
    print("\n" + "="*60)
    print("🚀 Starting Optimized API Server with Local Model, PDF Support & Memory...")
    print("="*60)
    uvicorn.run(app, host="localhost", port=8000)