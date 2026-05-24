# Schedule Extraction Workflow

เอกสารนี้อธิบายว่า `ScheduleExtractionService` ทำงานอย่างไรใน `/api/organize`,
ข้อมูลไหลจาก parser ไปถึง response แบบไหน และส่วนใดใช้ AI ในการแยกข้อมูลตารางเวลา

## ภาพรวม

`ScheduleExtractionService` เป็น best-effort analyzer สำหรับหา event ที่น่าจะเอาไปสร้าง
Google Calendar event ได้ เช่น meeting, appointment, flight หรือ itinerary

หลักการสำคัญ:

- backend ไม่สร้าง calendar event เอง
- backend ไม่เรียก Google API
- backend ไม่เก็บ schedule ลง database ใน v1
- backend แค่คืน `schedule` เป็น draft JSON กลับไปใน `OrganizeFileResult`
- frontend/Tauri เป็นคนให้ user confirm/edit แล้วค่อยเรียก Google Calendar API

ผลลัพธ์จึงเป็น candidate ไม่ใช่ committed event

## อยู่ตรงไหนใน organize pipeline

ตำแหน่งปัจจุบันของ schedule extraction อยู่หลัง fast parser และก่อน summary:

```text
scan file
  -> upsert files row
  -> cache check
  -> AI capability check
  -> ingest.prepare()
       -> fast Docling parse
       -> extracted_text
       -> content_list with page_idx
       -> queue rich RAG ingest in background
  -> ScheduleExtractionService.extract()
  -> SummaryService.summarise()
  -> RenameService.suggest_names()
  -> ClassificationService
  -> return OrganizeFileResult
```

เหตุผลที่วางก่อน summary คือ schedule ต้องการข้อมูลละเอียด เช่นวัน เวลา timezone flight number
หรือ attendee ซึ่ง summary อาจตัดทิ้งได้ ดังนั้น service นี้ใช้ raw parser output เป็นหลัก

## Parser ทำอะไรให้

`BackgroundIngestWorker.prepare()` ใช้ fast Docling parser เพื่อแปลงไฟล์เป็น `content_list`
และ `extracted_text`

ตัวอย่าง `content_list`:

```json
[
  {
    "type": "text",
    "text": "Project Sync meeting on 10 May 2026 at 14:00-15:00",
    "page_idx": 1
  }
]
```

`ScheduleExtractionService` ใช้ `content_list` เพราะมี `page_idx` ทำให้เลือกเฉพาะหน้าที่น่าสงสัยได้
ไม่จำเป็นต้องส่งเอกสารทั้งไฟล์เข้า LLM โดยเฉพาะไฟล์ยาวหลายสิบหรือหลายร้อยหน้า

ถ้าไม่มี `content_list` แต่มี `extracted_text` service จะ fallback ไปใช้ text รวมแทน

## ใช้ AI ไหม

ใช้ AI ครับ แต่ไม่ได้ใช้ตั้งแต่ขั้นแรก

ลำดับจริงคือ:

```text
1. ใช้ rule/regex หา schedule evidence ก่อน
2. ถ้าไม่เจอ evidence -> ไม่เรียก AI และคืน events=[]
3. ถ้าเจอ evidence -> เลือกเฉพาะ candidate pages/context
4. ส่ง context ที่ถูกตัดแล้วเข้า local LLM ผ่าน llm_client.achat()
5. ให้ LLM คืน JSON ตาม schema
6. validate JSON ด้วย Pydantic
7. คืน schedule candidates ให้ frontend
```

AI ที่ใช้คือ local `llama-server` ผ่าน `LlmClient` เหมือน summary/rename flow
ไม่ได้ส่งข้อมูลขึ้น cloud และไม่ได้ใช้ Google AI

## อะไรที่ไม่ใช้ AI

ส่วนเหล่านี้เป็น deterministic logic:

- group text/table ตาม `page_idx`
- หา keyword เช่น `meeting`, `agenda`, `flight`, `departure`, `arrival`, `ประชุม`, `เที่ยวบิน`
- หา date/time pattern เช่น `2026-05-10`, `14:00`, `10/05/2026`
- หา flight pattern เช่น `TG123`
- หา airport code pattern เช่น `BKK`, `NRT`
- เลือก candidate pages และหน้าใกล้เคียง
- limit context ไม่ให้เกิน budget
- parse/validate JSON response

ดังนั้น AI ทำหน้าที่เฉพาะ extraction จาก context ที่คัดมาแล้ว ไม่ใช่ scan ทั้งเอกสารแบบสุ่ม

## วิธีเลือก context

Service จะ group text ตามหน้า:

```text
page 1 -> text...
page 2 -> text...
page 3 -> text...
```

จากนั้นหา page ที่มี schedule evidence เช่น:

- keyword + date/time
- flight number
- keyword + airport code

ถ้าเจอ page ที่ match จะ include หน้านั้น และหน้าใกล้เคียง `page - 1`, `page + 1`
เพื่อเก็บ context ที่อาจมี location หรือ attendee อยู่คนละบรรทัด/คนละหน้า

สำหรับเอกสารสั้น ถ้าไม่มี page-level candidate แต่ text รวมมี schedule evidence
จะส่ง text รวมเข้า LLM ได้

สำหรับเอกสารยาว ถ้าไม่เจอ evidence จะไม่เรียก LLM เพื่อเลี่ยง latency, token cost และ hallucination

## Output contract

`OrganizeFileResult` มี field ใหม่แบบ optional:

```json
{
  "file_id": "file-123",
  "suggested_names": ["project-sync-agenda.pdf"],
  "categories": [],
  "error": null,
  "schedule": {
    "events": [
      {
        "type": "meeting",
        "confidence": 0.86,
        "source_pages": [1],
        "source_text": "Project Sync, 10 May 2026, 14:00-15:00",
        "missing_fields": [],
        "google_event": {
          "summary": "Project Sync",
          "description": "Extracted from project-sync-agenda.pdf",
          "location": "Google Meet",
          "start": {
            "dateTime": "2026-05-10T14:00:00+07:00",
            "timeZone": "Asia/Bangkok"
          },
          "end": {
            "dateTime": "2026-05-10T15:00:00+07:00",
            "timeZone": "Asia/Bangkok"
          },
          "attendees": [
            {
              "email": "person@example.com",
              "displayName": "Person"
            }
          ],
          "reminders": {
            "useDefault": true
          }
        }
      }
    ],
    "error": null
  }
}
```

`google_event` ตั้งใจให้คล้าย Google Calendar Events resource body เพื่อให้ frontend เอาไปใช้ต่อได้ง่าย
แต่ยังไม่ใช่ request ที่สมบูรณ์ทั้งหมด เพราะ frontend ยังต้องเติมสิ่งที่เป็น user/app decision เช่น:

- `calendarId`
- `sendUpdates`
- OAuth token
- Google Meet `conferenceDataVersion`
- user confirmation/editing

## Failure behavior

Schedule extraction เป็น non-critical step

ถ้า extract ไม่สำเร็จ:

- organize flow ยังทำ summary, rename และ classification ต่อ
- response จะมี `schedule.events=[]`
- response จะมี `schedule.error`
- backend เขียน system log event `organize_schedule_extraction_failed`

ถ้า LLM คืน JSON ผิด schema:

```json
{
  "schedule": {
    "events": [],
    "error": "Schedule extraction returned invalid JSON."
  }
}
```

## Cache behavior

ใน v1 schedule ไม่ถูก persist ลง database

ดังนั้น:

- full organize run จะมีโอกาสได้ `schedule`
- cached organize hit จะไม่ recompute schedule และ `schedule` จะไม่ถูกส่งกลับ
- ถ้า frontend ต้องการบังคับ extract ใหม่สำหรับไฟล์เดิม ให้เรียก `/api/organize` ด้วย `force=true`

เหตุผลคือการ persist schedule ต้องมี schema/migration และ policy เพิ่ม เช่น expiry, user confirmation state,
หรือ audit ว่า user สร้าง calendar event แล้วหรือยัง ซึ่งยังอยู่นอก scope ของ v1

## Frontend/Tauri responsibility

หลัง backend คืน `schedule.events` แล้ว frontend ควร:

1. แสดง candidate ให้ user ตรวจ
2. ให้ user แก้ title/time/location/attendees ได้
3. เลือก calendar ปลายทาง
4. ทำ OAuth/Google Calendar API ใน Tauri
5. เรียก Google Calendar `events.insert`

backend ไม่ควรถือ Google token และไม่ควรสร้าง event อัตโนมัติ เพราะ calendar creation เป็น side effect
ที่ควรเกิดหลัง user ยืนยันเท่านั้น

## ข้อจำกัดของ v1

- รองรับเฉพาะ text/table ที่ Docling extract ได้
- image-only PDF จะพึ่ง fast parser ไม่ได้ดีถ้า OCR ปิด
- default timezone ใน prompt คือ `Asia/Bangkok` ถ้าเอกสารไม่ระบุ timezone
- LLM อาจพลาดหรือ format ผิด จึงต้อง validate และให้ user confirm เสมอ
- ยังไม่มี persistent schedule cache
- ยังไม่มี dedicated endpoint สำหรับ re-scan schedule อย่างเดียว

## ไฟล์หลักที่เกี่ยวข้อง

- `app/services/organize/schedule_extraction_service.py`
- `app/services/organize/background_ingest.py`
- `app/services/organize/organize_pipeline.py`
- `app/models/response.py`
- `app/api/organize.py`
