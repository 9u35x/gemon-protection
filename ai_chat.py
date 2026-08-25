from __future__ import annotations
import json, logging, os, urllib.error, urllib.request
from typing import Optional
log = logging.getLogger("jaimon.ai")
OPENROUTER_API_KEY = (os.getenv("OPENROUTER_API_KEY") or "").strip()
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()
GROQ_API_KEY = (os.getenv("GROQ_API_KEY") or "").strip()
GROQ_MODELS = ["openai/gpt-oss-20b", "allam-2-7b", "openai/gpt-oss-120b", "qwen/qwen3.6-27b"]
OPENROUTER_MODELS = [m for m in [(os.getenv("AI_MODEL") or "").strip(),"meta-llama/llama-3.2-3b-instruct:free","google/gemma-2-9b-it:free","microsoft/phi-3-mini-128k-instruct:free"] if m]
SYSTEM = "أنت جيمون، مساعد ذكي في تليجرام. أجب بالعربية باختصار ووضوح بطابع عراقي خفيف."
def is_ai_ready() -> bool:
    return bool(OPENROUTER_API_KEY or GEMINI_API_KEY or GROQ_API_KEY)
def provider_status() -> str:
    return f"OpenRouter: {'✅' if OPENROUTER_API_KEY else '❌'} | Gemini: {'✅' if GEMINI_API_KEY else '❌'} | Groq: {'✅' if GROQ_API_KEY else '❌'}"
def _post(url, headers, payload):
    headers = dict(headers)
    headers.setdefault("User-Agent", "Mozilla/5.0 (compatible; GemonBot/1.0)")
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return 200, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return 0, str(e)
def _ask_openrouter(text: str) -> str:
    if not OPENROUTER_API_KEY: return ""
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json", "HTTP-Referer": "https://t.me/", "X-Title": "Gemon Bot"}
    last = ""
    for model in OPENROUTER_MODELS:
        payload = {"model": model, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text[:4000]}], "temperature": 0.7, "max_tokens": 1024}
        code, body = _post(url, headers, payload)
        if code == 200 and isinstance(body, dict):
            try:
                out = (body["choices"][0]["message"]["content"] or "").strip()
                if out: return out[:3500]
            except Exception: pass
        last = str(code)
        if code in (401, 403): return "❌ مفتاح OpenRouter غير صالح — openrouter.ai/keys"
        if code == 429: return "⚠️ حد OpenRouter مؤقتًا."
    return f"❌ OpenRouter فشل ({last})"
def _ask_gemini(text: str) -> str:
    if not GEMINI_API_KEY: return ""
    for model in ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash", "gemini-2.0-flash-001", "gemini-1.5-flash", "gemini-1.5-flash-latest", "gemini-1.5-pro", "gemini-pro"]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        payload = {"contents": [{"role": "user", "parts": [{"text": SYSTEM + "\n\n" + text}]}], "generationConfig": {"temperature": 0.7, "maxOutputTokens": 1024}}
        code, body = _post(url, {"Content-Type": "application/json"}, payload)
        if code == 200 and isinstance(body, dict):
            try:
                parts = body["candidates"][0]["content"]["parts"]
                out = "".join(p.get("text", "") for p in parts).strip()
                if out: return out[:3500]
            except Exception: pass
        if code == 404: continue
        if code in (400, 403): return "❌ مفتاح Gemini غير صالح."
    return "❌ Gemini فشل."
def ask_ai(user_text: str, history: Optional[list] = None) -> str:
    text = (user_text or "").strip()
    if not text: return "اكتب سؤالك بعد: جيمون"
    if not is_ai_ready():
        return "❌ لا يوجد مفتاح.\nRailway → OPENROUTER_API_KEY\nhttps://openrouter.ai/keys"
    err = "❌ تعذر الاتصال."
    for fn in (_ask_groq, _ask_openrouter, _ask_gemini):
        r = fn(text)
        if r and not r.startswith("❌") and not r.startswith("⚠️"): return r
        if r: err = r
    return err

def _ask_groq(text: str) -> str:
    log.info(f"GROQ_KEY_SET={bool(GROQ_API_KEY)}")
    if not GROQ_API_KEY: return ""
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    last = ""
    for model in GROQ_MODELS:
        log.info(f"trying groq model={model}")
        payload = {"model": model, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text[:4000]}], "temperature": 0.7, "max_tokens": 1024}
        code, body = _post(url, headers, payload)
        log.info(f"groq: model={model} code={code} body={str(body)[:200]}")
        if code == 200 and isinstance(body, dict):
            try:
                out = (body["choices"][0]["message"]["content"] or "").strip()
                if out: return out[:3500]
            except Exception: pass
        last = str(code)
        if code in (401, 403): return "❌ مفتاح Groq غير صالح — console.groq.com/keys"
        if code == 429: return "⚠️ حد Groq مؤقتًا."
    return f"❌ Groq فشل ({last})"
