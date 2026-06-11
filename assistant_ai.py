# assistant_ai.py
#ChatGPT, Help me incorporate machine learning into the Catchat for knowledge based questions, 10/20/25
from __future__ import annotations
import os, json, sqlite3, time
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import Pipeline

try:
    import faiss  # optional
    HAVE_FAISS = True
except Exception:
    HAVE_FAISS = False

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)
DOCS_JSONL = os.path.join(DATA_DIR, "ua_chunks.jsonl")
EMB_PATH   = os.path.join(DATA_DIR, "ua_embeddings.npy")
CLASSIFIER_PATH = os.path.join(DATA_DIR, "intent_clf.pkl")
MEMO_DB    = os.path.join(DATA_DIR, "memory.db")
#ChatGPT, Implement Short-term memory and resolve import issues in App.py, 11/11/25
SYSTEM_PROMPT = ( #ChatGPT, Implement Short-term memory and resolve import issues in App.py, 11/11/25
    "You are CatChat, a concise, helpful university info assistant. "
    "When unsure, say so briefly and suggest an official source. "
    "Keep answers short and point to sources when available."
)


# ---------- memory (unknown queries + feedback) ----------
def _db():
    con = sqlite3.connect(MEMO_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS unknown_queries(
        id INTEGER PRIMARY KEY, query TEXT UNIQUE, count INTEGER DEFAULT 1, created_at INTEGER)""")
    con.execute("""CREATE TABLE IF NOT EXISTS feedback(
        id INTEGER PRIMARY KEY, query TEXT, answer TEXT, rating TEXT, created_at INTEGER)""")
    con.commit()
    return con

def record_unknown(q: str):
    con = _db()
    cur = con.cursor()
    cur.execute("SELECT id,count FROM unknown_queries WHERE query=?", (q.strip(),))
    row = cur.fetchone()
    if row: cur.execute("UPDATE unknown_queries SET count=? WHERE id=?", (row[1]+1, row[0]))
    else:   cur.execute("INSERT INTO unknown_queries(query,count,created_at) VALUES(?,?,?)",
                        (q.strip(), 1, int(time.time())))
    con.commit(); con.close()

def record_feedback(query: str, answer: str, rating: str):
    con = _db()
    con.execute("INSERT INTO feedback(query,answer,rating,created_at) VALUES(?,?,?,?)",
                (query, answer, rating, int(time.time())))
    con.commit(); con.close()

# ---------- intent classifier ----------
INTENTS = ["admissions_ug","admissions_grad","advising_ug","advising_grad",
           "careers","tuition","deadlines","housing","parking","contact","unknown"]

@dataclass
class Ex: text: str; label: str

def _seed() -> List[Ex]:
    seeds = {
      "admissions_ug": ["undergrad admissions","freshman requirements","transfer to ua se"],
      "admissions_grad": ["gradapp deadline","ms se requirements","phd application"],
      "advising_ug": ["undergrad advisor contact","book ugrad advising"],
      "advising_grad": ["graduate advisor email","ms advising office"],
      "careers": ["software engineering careers","internship software"],
      "tuition": ["out of state tuition","semester fees"],
      "deadlines": ["application deadline","priority date"],
      "housing": ["dorms for freshmen","on-campus housing"],
      "parking": ["parking permit price","campus parking map"],
      "contact": ["department phone","ece office number"],
      "unknown": ["tell me a joke","weather tucson"]
    }
    out=[]; [out.append(Ex(t,l)) for l,arr in seeds.items() for t in arr]; return out

class Intent:
    def __init__(self): self.pipe: Optional[Pipeline]=None
    def train_or_load(self, path=CLASSIFIER_PATH):
        import joblib, os
        if os.path.exists(path): self.pipe = joblib.load(path); return
        data=_seed(); X=[e.text for e in data]; y=[e.label for e in data]
        self.pipe = Pipeline([("tfidf",TfidfVectorizer(ngram_range=(1,2))),("lr",LogisticRegression(max_iter=1000))])
        self.pipe.fit(X,y); joblib.dump(self.pipe, path)
    def predict(self, text:str)->Tuple[str,float]:
        if not self.pipe: self.train_or_load()
        probs=self.pipe.predict_proba([text])[0]; labels=self.pipe.classes_
        i=int(np.argmax(probs)); return labels[i], float(probs[i])

# ---------- RAG index ----------
class RAG:
    def __init__(self, model="sentence-transformers/all-MiniLM-L6-v2", min_score: float = 0.35):
        self.m = SentenceTransformer(model)
        self.docs: List[Dict] = []
        self.emb: Optional[np.ndarray] = None
        self.idx = None
        self.min_score = min_score
    def load(self):
        if not os.path.exists(DOCS_JSONL): self.docs=[]; self.emb=None; return
        self.docs = [json.loads(l) for l in open(DOCS_JSONL,"r",encoding="utf-8")]
        if os.path.exists(EMB_PATH):
            self.emb = np.load(EMB_PATH)
        else:
            self.emb = self.m.encode([d["text"] for d in self.docs], normalize_embeddings=True)
            np.save(EMB_PATH, self.emb)
        if HAVE_FAISS:
            dim=self.emb.shape[1]; self.idx=faiss.IndexFlatIP(dim)
            self.idx.add(self.emb.astype(np.float32))
    def search(
        self,
        q: str,
        k: int = 5,
        min_score: Optional[float] = None,
        include_raw: bool = False,
    ):
        if not self.docs or self.emb is None:
            empty = ([], []) if include_raw else []
            return empty
        threshold = self.min_score if min_score is None else min_score
        qv = self.m.encode([q], normalize_embeddings=True)[0]
        if self.idx is not None:
            D, I = self.idx.search(np.array([qv], dtype=np.float32), k)
            idxs = I[0].tolist()
            sims = D[0].tolist()
        else:
            sims_all = (self.emb @ qv).tolist()
            idxs = np.argsort(sims_all)[::-1][:k].tolist()
            sims = [sims_all[i] for i in idxs]

        out = []
        raw_hits = []
        for rank, (i, score) in enumerate(zip(idxs, sims), 1):
            d = self.docs[i]
            entry = {
                "rank": rank,
                "score": float(score),
                "text": d["text"],
                "url": d.get("meta", {}).get("url"),
                "source": d.get("meta", {}).get("source", "ua")
            }
            raw_hits.append(entry)
            if threshold is not None and score < threshold:
                continue  # drop weak matches
            out.append(entry)
        if include_raw:
            return out, raw_hits
        return out

# ---------- answer composer (no external API required) ----------
class Composer:
    def __init__(self, max_bullets: int = 1):
        self.max_bullets = max_bullets

    def compose(self, query: str, hits: List[Dict]) -> str:
        if not hits:
            record_unknown(query)
            return ("I couldn't quite match that yet. Could you clarify what you're looking for, "
                    "or rephrase it with a few more details? You can also type 'cancel' to stop.")
        lines = []
        for h in hits[:self.max_bullets]:
            cite = f' <a href="{h.get("url")}" target="_blank" rel="noopener">[source]</a>' if h.get("url") else ""
            lines.append(f"{h['text']}{cite}")
        return "Here's what I found:\n" + "\n".join(lines)

    def compose_clarification(self, options: List[Dict]) -> str:
        if not options:
            return ("I need a little more detail to help. Could you rephrase your question "
                    "or tell me what you're looking for?")
        lines = [
            "I found a couple of equally close matches and need clarification before I answer:"
        ]
        for idx, opt in enumerate(options, 1):
            cite = f' <a href="{opt.get("url")}" target="_blank" rel="noopener">[source]</a>' if opt.get("url") else ""
            lines.append(f"{idx}. {opt.get('summary', '').strip()}{cite}")
        lines.append("Let me know which one fits best, rephrase it, or type 'cancel' to stop.")
        return "\n".join(lines)

    def compose_unclear(self, hints: List[Dict]) -> str:
        lines = [
            "I'm not confident I understood that last request and want to make sure I get it right. I have logged your question for possible updates in the future."
        ]
        if hints:
            lines.append("These were the closest matches, but they fell below my knowledge threshold:")
            for idx, hint in enumerate(hints, 1):
                cite = f' <a href="{hint.get("url")}" target="_blank" rel="noopener">[source]</a>' if hint.get("url") else ""
                summary = (hint.get("summary") or "General program information").strip()
                lines.append(f"{idx}. {summary}{cite}")
        lines.append("Could you clarify or correct me? You can also type 'cancel' if you'd prefer to stop.")
        return "\n".join(lines)

# ---------- public façade ----------
class ChatAI:
    CLARIFY_TOLERANCE = 0.015  # similarity gap before assuming responses diverge
    CLARIFY_MIN_SCORE = 0.25   # don't ask unless hits are at least near the threshold
    CLARIFY_MAX_OPTIONS = 3

    def __init__(self):
        _db()  # ensure tables
        self.intent = Intent(); self.intent.train_or_load()
        self.rag = RAG(); self.rag.load()
        self.compose = Composer()
    #ChatGPT, Add more human touch to the CatChat Bot, 11/15/25
    def handle(self, raw: str) -> Dict:
        text = (raw or "").strip()
        if not text:
            return {
                "answer": "I didn’t catch anything in that last message. Could you type it again?",
                "intent": "empty",
                "confidence": 1.0,
            }

        low = text.lower()

        # --- META INTENT: simple chit-chat / social niceties ---
        meta = self._meta_intent(low)
        if meta is not None:
            ans = self._meta_answer(meta, text)
            return {
                "answer": ans,
                "intent": meta,
                "confidence": 1.0,
            }

        # --- normal intent + RAG pipeline ---
        label, conf = self.intent.predict(text)
        hits, raw_hits = self.rag.search(text, k=5, include_raw=True)
        if hits:
            ans = self.compose.compose(text, hits)
        else:
            record_unknown(text)
            options = self._clarification_options(raw_hits)
            if options:
                ans = self.compose.compose_clarification(options)
            else:
                hints = self._low_confidence_hints(raw_hits)
                ans = self.compose.compose_unclear(hints)
        return {"answer": ans, "intent": label, "confidence": conf}

    # ChatGPT, Add more human touch to the CatChat Bot, 11/15/25
    @staticmethod
    def _meta_intent(low: str) -> Optional[str]:
        """Lightweight, rule-based detector for chit-chat / social intents."""
        if not low:
            return None

        # thanks / appreciation
        thanks = [
            "thank you",
            "thanks",
            "thx",
            "ty",
            "thank u",
            "thanks so much",
            "thank you so much",
            "appreciate your help",
            "i appreciate it",
        ]
        if any(p in low for p in thanks):
            return "smalltalk_thanks"

        # greetings
        greetings = [
            "hi",
            "hello",
            "hey",
            "hiya",
            "good morning",
            "good afternoon",
            "good evening",
        ]
        # avoid treating things like "high" as a greeting by checking word boundaries a bit
        if any(low.startswith(p) or f" {p}" in low for p in greetings):
            return "smalltalk_greeting"

        # farewells
        farewells = [
            "bye",
            "goodbye",
            "see you",
            "see ya",
            "take care",
            "talk to you later",
        ]
        if any(p in low for p in farewells):
            return "smalltalk_farewell"

        # “how are you” smalltalk
        if "how are you" in low or "how's it going" in low or "hows it going" in low:
            return "smalltalk_how_are_you"

        # identity / capability questions
        if "who are you" in low or "what are you" in low or "what can you do" in low:
            return "bot_identity"

        # light “tell me a joke” handling (optional)
        if "tell me a joke" in low or ( "joke" in low and "tell" in low ):
            return "smalltalk_joke"

        return None

    @staticmethod
    def _meta_answer(tag: str, original: str) -> str:
        """Pre-baked, friendly answers for meta-intents."""
        if tag == "smalltalk_thanks":
            return (
                "You’re very welcome! 😊<br>"
                "If you think of more questions about advisors, admissions, careers, or research centers, "
                "I’m here to help."
            )

        if tag == "smalltalk_greeting":
            return (
                "Hi there! 👋 I’m ChatCat, the Software Engineering program assistant.<br>"
                "You can ask me about advisors, admissions, careers & internships, or research centers."
            )

        if tag == "smalltalk_farewell":
            return (
                "Glad I could help today. 👋<br>"
                "Come back anytime if you have more questions about the Software Engineering program."
            )

        if tag == "smalltalk_how_are_you":
            return (
                "I’m doing great, thanks for asking! 😺<br>"
                "I stay pretty busy helping students with advisors, admissions, careers, and research questions."
            )

        if tag == "bot_identity":
            return (
                "I’m ChatCat, a virtual assistant for the UA Software Engineering program. 😸<br>"
                "I can help you with:<br>"
                "• Undergraduate and graduate advising info<br>"
                "• Admissions requirements and deadlines<br>"
                "• Careers & internships in software engineering<br>"
                "• Research centers and opportunities"
            )

        if tag == "smalltalk_joke":
            return (
                "Here’s a quick one: 😄<br>"
                "Why do programmers prefer dark mode?<br>"
                "Because light attracts bugs."
            )

        # Fallback (shouldn’t normally hit)
        return (
            "Thanks for chatting with me! If you have questions about advisors, admissions, careers, "
            "or research centers, just let me know."
        )


    def _clarification_options(self, raw_hits: List[Dict]) -> List[Dict]:
        if len(raw_hits) < 2:
            return []
        top_score = raw_hits[0]["score"]
        if top_score < self.CLARIFY_MIN_SCORE:
            return []
        options = []
        for hit in raw_hits:
            if len(options) >= self.CLARIFY_MAX_OPTIONS:
                break
            if abs(hit["score"] - top_score) > self.CLARIFY_TOLERANCE:
                break
            options.append({
                "summary": self._summarize(hit.get("text", "")),
                "url": hit.get("url"),
                "source": hit.get("source"),
            })
        return options if len(options) >= 2 else []

    @staticmethod
    def _summarize(text: str, limit: int = 140) -> str:
        clean = " ".join((text or "").split())
        if not clean:
            return "General program information"
        if len(clean) <= limit:
            return clean
        clipped = clean[:limit].rstrip()
        if " " in clipped:
            clipped = clipped.rsplit(" ", 1)[0]
        return clipped + "..."

    def _low_confidence_hints(self, raw_hits: List[Dict]) -> List[Dict]:
        hints = []
        if not raw_hits:
            return hints
        for hit in raw_hits[:self.CLARIFY_MAX_OPTIONS]:
            hints.append({
                "summary": self._summarize(hit.get("text", "")),
                "url": hit.get("url"),
                "source": hit.get("source"),
            })
        return hints

# Utilities exposed for Flask
def ai_answer(raw_text:str)->str:
    global _AI
    try:
        _AI
    except NameError:
        _AI = ChatAI()
    return _AI.handle(raw_text)["answer"]


#ChatGPT, Implement short-term memory and resolve import error on app.py, 11/11/25
def ai_answer_messages(messages: List[Dict]) -> Optional[str]:
    """
    Adapter for chat-style calls. Flattens messages into a single prompt and
    reuses the existing ai_answer() pipeline (intent + RAG + compose).
    """
    try:
        # Basic flattening: preserve message order and roles for traceability
        flattened = "\n\n".join(
            f"{(m.get('role','user') or 'user').upper()}: {m.get('content','')}"
            for m in (messages or [])
        )
        return ai_answer(flattened)
    except Exception:
        return None

#ChatGPT, We have implemented short-term memory, next we want to implement some sort of AI steering tool that will check to see if the person got all their info or provide other possible information. 11/13/25
# --- new helper for steering ---
def ai_handle_messages(messages: List[Dict]) -> Optional[Dict]:
    """
    Chat-style adapter that returns full AI info:
    {
      "answer": str,
      "intent": str,
      "confidence": float,
    }
    """
    try:
        flattened = "\n\n".join(
            f"{(m.get('role','user') or 'user').upper()}: {m.get('content','')}"
            for m in (messages or [])
        )
        # Reuse existing ChatAI instance
        global _AI
        try:
            _AI
        except NameError:
            _AI = ChatAI()
        return _AI.handle(flattened)
    except Exception:
        return None

def save_feedback(query:str, answer:str, rating:str):
    record_feedback(query, answer, rating)
