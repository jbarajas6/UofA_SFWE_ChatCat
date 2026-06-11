from flask import Flask, request, render_template, session
from flask_session import Session
import re, html
from markupsafe import Markup, escape
from typing import Optional
from assistant import AdmissionsAssistant
from assistant_ai import ai_answer, save_feedback, ai_handle_messages
from messages import get_welcome_message, get_official_links, get_grad_advisor_html
from memory import get_recent_memory, add_to_memory, clear_memory #ChatGPT, Add Short-term memory to CatChat, 11/11/2025
from assistant_ai import ai_answer_messages, SYSTEM_PROMPT, ai_handle_messages #ChatGPT, Add Short-term memory to CatChat, 11/11/2025
import logging #ChatGPT, How do I test the short-term memory is functional?, 11/11/25
logging.basicConfig(level=logging.INFO) #ChatGPT, How do I test the short-term memory is functional?, 11/11/25
log = logging.getLogger("cactchat") #ChatGPT, How do I test the short-term memory is functional?, 11/11/25



#ChatGPT, Implement User Satisfaction Check 11/13/25
def user_seems_satisfied(user_input: str) -> bool:
    """
    Heuristic to detect if the user is already satisfied so we
    can tone down or skip steering.
    """
    low = (user_input or "").lower()
    return any(
        phrase in low
        for phrase in [
            "thank you",
            "thanks",
            "that helps",
            "that answered my question",
            "you answered my question",
            "got it",
            "perfect",
            "awesome, thanks",
            "this is helpful",
            "that was helpful",
        ]
    )

#ChatGPT, Help steer the user by providing some follow-up responses, 11/15/25
def build_steering_suggestion(
    intent: str,
    user_input: str,
    recent: list[dict],
    confidence: Optional[float] = None,
):
    """
    Lightweight 'AI steering' layer.

    Uses:
      - current intent (from intent classifier)
      - recent short-term memory (recent user messages)
      - model confidence (if available)
    to propose follow-up topics.

    Returns:
      - Markup HTML snippet (buttons + text), or
      - "" if nothing useful to suggest
    """
    # If they already sound satisfied, just give a soft closing note.
    if user_seems_satisfied(user_input):
        return Markup(
            "<br><br><i>Glad that helped! "
            "If you need anything else about admissions, advising, or campus life, just ask.</i>"
        )

    # If the model isn't very confident, don't aggressively steer.
    if confidence is not None and confidence < 0.35:
        return ""  # keep it simple; let the user drive follow-ups

    # Build a text window from the last few turns + current input
    window_msgs = (recent or [])[-6:]  # last ~6 messages in short-term memory
    text_window = " ".join((m.get("content", "") or "").lower() for m in window_msgs)
    text_window += " " + (user_input or "").lower()

    suggestions: list[str] = []

    # --- Admissions: undergraduate ---
    if intent in {"admissions_ug", "admissions"}:
        if "deadline" not in text_window:
            suggestions.append("BS Software Engineering application deadlines")
        if "requirement" not in text_window and "document" not in text_window:
            suggestions.append("undergraduate admission requirements and documents")
        if "transfer" not in text_window:
            suggestions.append("transfer credit and articulation for BS Software Engineering")

    # --- Admissions: graduate / PhD ---
    elif intent == "admissions_grad":
        if "deadline" not in text_window:
            suggestions.append("priority deadlines for the graduate program")
        if "funding" not in text_window and "assistantship" not in text_window:
            suggestions.append("funding, assistantships, and fellowships")
        if "phd" not in text_window and "doctoral" not in text_window:
            suggestions.append("differences between MS and PhD paths")

    # --- Advising (UG & Grad) ---
    elif intent in {"advising_ug", "advising_grad"}:
        if "appointment" not in text_window and "schedule" not in text_window:
            suggestions.append("how to schedule an advising appointment")
        if "hold" not in text_window:
            suggestions.append("common registration holds and how to clear them")
        if "plan" not in text_window and "roadmap" not in text_window:
            suggestions.append("building a 2-year or 4-year academic plan")

    # --- Careers / internships ---
    elif intent == "careers":
        if "internship" not in text_window:
            suggestions.append("finding internships for software engineering")
        if "resume" not in text_window and "cv" not in text_window:
            suggestions.append("resume and cover letter tips")
        if "career services" not in text_window:
            suggestions.append("Career Services support for software engineering students")

    # --- Research / labs / centers ---
    elif intent == "research":
        if "center" not in text_window and "lab" not in text_window:
            suggestions.append("research centers affiliated with software engineering")
        if "undergraduate research" not in text_window and "urop" not in text_window:
            suggestions.append("undergraduate research opportunities")
        if "faculty" not in text_window:
            suggestions.append("how to contact faculty about research")

    # You can add more intents (housing, tuition, etc.) here as needed.

    # De-duplicate and cap to 3
    seen = set()
    deduped: list[str] = []
    for s in suggestions:
        if s not in seen:
            seen.add(s)
            deduped.append(s)

    suggestions = deduped[:3]
    if not suggestions:
        return ""

    # Render as little POST-back buttons so the user can click instead of retyping
    forms_html: list[str] = []
    for label in suggestions:
        safe_val = html.escape(label)
        forms_html.append(
            "<form method='post' style='display:inline;margin-right:6px;'>"
            f"<input type='hidden' name='message' value=\"{safe_val}\">"
            f"<button type='submit'>{html.escape(label.title())}</button>"
            "</form>"
        )

    snippet = (
        "<br><br>"
        "<i>Students in your situation often also ask about:</i><br>"
        + " ".join(forms_html)
    )
    return Markup(snippet)




class ChatState:
    DEFAULT = "default"
    ASKING_ADVISOR_TYPE = "asking_advisor_type"
    ASKING_ADMISSIONS_TYPE = "asking_admissions_type"
    SPECIFIC_ADVISOR_TYPE = "specific_advisor_type"
    ASKING_RESEARCH = "asking_research"
    ASKING_MS_SFWE = "asking_ms_sfwe"

app = Flask(__name__)
app.config["SECRET_KEY"] = "supersecretkey"
app.config["SESSION_TYPE"] = "filesystem"
Session(app)


BR_TAG_RE = re.compile(r"<br\s*/?>", re.I)

def ai_allowed() -> bool:
    # Only allow AI when we are not in any guided flow
    flow = session.get("admissions_flow") or {}
    in_flow = isinstance(flow, dict) and bool(flow.get("stage"))
    return session.get("state") == ChatState.DEFAULT and not in_flow


@app.route("/", methods=["GET", "POST"])
def home():
    # ensure session keys exist
    session.setdefault("history", [])
    session.setdefault("state", ChatState.DEFAULT)
    #ChatGPT, The AI Fallback is duplicating. Help identify the areas of concern, 11/11/25
    session.setdefault("last_user_msg", None)
    session.setdefault("last_bot_msg", None)

    # If first visit or no history, show welcome + links once
    if not session["history"]:
        session["history"].append(("ChatCat", get_welcome_message()))
        session["history"].append(("ChatCat", get_official_links()))

    # Minimal reset support for the modern UI
    if request.method == "POST" and request.form.get("message", "").strip().lower() in {
        "reset",
        "/reset",
        "clear",
    }:
        session["history"].clear()
        session["state"] = ChatState.DEFAULT
        session.pop("admissions_flow", None)
        # ChatGPT, Add Short-Term Memory, 11/11/2025
        try:
            clear_memory()
        except Exception:
            pass
        ###
            # Show welcome and official links again after reset
        session["history"].append(("ChatCat", get_welcome_message()))
        session["history"].append(("ChatCat", get_official_links()))
        return render_template("index.html", history=session["history"])

    if request.method == "POST":
        # ChatGPT, The AI Fallback is duplicating. Help identify the areas of concern, 11/11/25
        raw_input = request.form.get("message", "")
        user_input = raw_input.lower().strip()
        bot_response: Optional[str] = None
        response: Optional[str] = None  # <-- add this line

        # If the user sent the exact same message and our last bot reply already exists, avoid re-calling fallback immediately
        if user_input and user_input == (session.get("last_user_msg") or ""):
            # Just re-render; user likely double-submitted or clicked again
            return render_template("index.html", history=session["history"])
        session["last_user_msg"] = user_input

        # ChatGPT, Add Short-Term Memory, 11/11/2025
        # --- NEW: pre-log the user message to "default" memory if NOT in admissions ---
        # (Admissions messages are already mirrored by AdmissionsAssistant)
        # We'll decide "in_admissions" right after we compute admissions_triggers (below).

        admissions_triggers = bool(
            re.search(
                r"\b(admission|admissions|apply|application|deadline|deadlines|graduate\s+admissions|grad\s+admissions)\b",
                user_input,
            )
        )

        # Are we currently inside an admissions flow?
        in_flow_now = (
                isinstance(session.get("admissions_flow"), dict)
                and session.get("admissions_flow", {}).get("stage") is not None
        )
        in_admissions = in_flow_now

        # ChatGPT, How do I test the short-term memory is functional?, 11/11/25
        log.info("msg=%r admissions_triggers=%s in_admissions=%s", raw_input, admissions_triggers, in_admissions)

        def reply(resp):
            log.info("reply() called with: %r", str(resp)[:120])
            cleaned = None

            #ChatGPT, Some responses are not showing when initializing at the beginning. 11/28/25
            # Always record the user message first
            if not session["history"] or session["history"][-1] != ("You", raw_input):
                session["history"].append(("You", raw_input))
            # De-dupe identical bot replies (prevents loops)
            if isinstance(resp, str):
                cleaned = resp.strip()
                if session.get("last_bot_msg") == cleaned:
                    return render_template("index.html", history=session["history"])

            # Record user message in history once
            if not session["history"] or session["history"][-1] != ("You", raw_input):
                session["history"].append(("You", raw_input))

            # Add bot reply to history
            session["history"].append(("ChatCat", resp))
            session["last_bot_msg"] = (resp or "").strip()
            log.info("memory:add assistant->default: %r", str(resp)[:120])

            # Save assistant message to short-term memory
            try:
                if not admissions_triggers and cleaned:
                    if session.get("last_bot_msg") != cleaned:
                        add_to_memory("assistant", cleaned, state_name="default")
            except Exception:
                pass

            # ChatGPT, Add Short-Term Memory, 11/11/2025
            # Save user message to short-term memory
            if not admissions_triggers:
                try:
                    add_to_memory("user", raw_input, state_name="default")
                except Exception:
                    pass

            # Always render the page at the end
            return render_template("index.html", history=session["history"])

        # Allow users to exit any sub-flow without resetting history
        escape_cmds = {"exit", "cancel", "back", "menu", "main", "main menu", "stop", "quit", "home"}

        greetings = {
            "hi",
            "hello",
            "hey",
            "yo",
            "hiya",
            "hola",
            "good morning",
            "good afternoon",
            "good evening",
        }

        # Normalize punctuation so things like "Hello!" or "hello," match "hello"
        normalized = re.sub(r"[^\w\s]", "", user_input).strip()

        if normalized in greetings:
            bot_response = (
                "Hi! I can help with advisors, admissions, careers, or research centers. "
                "Try something like “Who are the undergraduate advisors?” or “Grad application deadlines”."
            )
            return reply(bot_response)

        # --- Gratitude detection ---
        #ChatGPT, We need to recognize when students thank the system for helping, 11/15/25.
        gratitude_phrases = [
            "thank you",
            "thanks",
            "thx",
            "thank u",
            "thanks so much",
            "thank you so much",
            "i appreciate it",
            "appreciate your help",
        ]
        if any(p in user_input for p in gratitude_phrases):
            # optionally: gently reset to default state
            session["state"] = ChatState.DEFAULT
            session["admissions_flow"] = {}

            response = ("You're very welcome! 😊<br>"
                        "If you have more questions about advisors, admissions, careers, or research centers, "
                        "just let me know.")
            return reply(response)

        if user_input.strip() in escape_cmds or user_input.strip("/") in escape_cmds:
            session["state"] = ChatState.DEFAULT
            # Clear admissions sub-flow state if present
            if isinstance(session.get("admissions_flow"), dict):
                session["state"] = ChatState.DEFAULT
                session["admissions_flow"] = {}  # fully clear
                session["admissions_flow"]["topic"] = None
            return reply(
                    "Okay, returning to the main menu. You can ask about careers, advisors, admissions, or research centers.",)

        # --- Error Handling (Requirement 7.1.3) ---
        # Detects invalid, empty, or nonsensical user messages and returns a helpful response
        if not user_input or len(user_input) < 2:
            fallback_response = Markup(
                "Sorry, I couldn’t process your request.<br>"
                "It might be outside my current knowledge or phrased in a way I don’t understand.<br><br>"
                "Here are a few things you can try:<br>"
                "• Rephrase your question using different keywords (e.g., 'advisor' or 'admissions').<br>"
                "• Visit the <a href='https://ece.engineering.arizona.edu/software-engineering-program' target='_blank'>University of Arizona Software Engineering site</a>.<br>"
                "• Or contact a live advisor at <a href='mailto:sfwe-advising@arizona.edu'>sfwe-advising@arizona.edu</a>."
            )
            return reply(fallback_response)

        phd_terms = [
            "phd", "ph.d", "doctoral", "dissertation", "qualifying exam", "qualifier", "qe",
            "proposal", "defense", "funding", "assistantship", "ra", "ta", "gta", "gsa", "fellowship"
        ]

        flow_state = session.get("admissions_flow")
        if not isinstance(flow_state, dict):
            flow_state = {}
        active_stage = flow_state.get("stage")
        flow_active = active_stage not in {None, "", "done", "idle"}
        in_admissions = admissions_triggers or flow_active

        #ChatGPT, Create handoff from admissions to ML handler, 11/15/25
        if in_admissions:
            bot = AdmissionsAssistant(
                history=session.get("history", []),
                admissions_flow=session.get("admissions_flow", {}),
            )
            bot.handle_message(raw_input)
            new_state = bot.get_state()
            session["history"] = new_state.get("history", [])
            session["admissions_flow"] = new_state.get("admissions_flow", {})

            flow = session.get("admissions_flow") or {}
            handoff = flow.get("handoff")

            # If the admissions assistant asked for a handoff, clear the flow and call AI
            if handoff:
                session["admissions_flow"] = {}
                try:
                    ai_resp = ai_answer(raw_input)
                except Exception:
                    ai_resp = None

                if ai_resp and (not isinstance(ai_resp, str) or ai_resp.strip()):
                    session["history"].append(("ChatCat", ai_resp))

                return render_template("index.html", history=session["history"])

            # Normal flow cleanup
            if not flow.get("stage") or flow.get("stage") in {"done", "idle", None}:
                session["admissions_flow"] = {}

            return render_template("index.html", history=session["history"])

        # make change here
        # BS SFWE program information (requirements, prereqs, plans, electives, transfer, registration)
        low = user_input
        bs_program_markers = [
            "bs software engineering",
            "bs software eng",
            "bs sfwe",
            "b.s. software engineering",
            "software engineering bs",
            "software engineering program",
            "software engineering degree",
            "software engineering major",
            "software eng program",
            "software eng degree",
            "sfwe program",
            "sfwe degree",
            "sfwe major",
            "bachelors",
            "bachelor",
            "bachelor's",
            "bachelor of science in software engineering",
            "software engineering bachelor's",
            "software engineering bachelors",
            "undergraduate software engineering",
            "undergrad software engineering",
            "undergraduate sfwe",
            "se program",
        ]
        mentions_bs_program = any(k in low for k in bs_program_markers)
        mentions_sfwe_keyword = any(
            k in low
            for k in [
                "sfwe",
                "software engineering",
                "software eng",
                "software program",
            ]
        )

        plan_keywords = [
            "4-year",
            "4 year",
            "plan",
            "planning guide",
            "roadmap",
            "sequence",
        ]
        has_plan_keyword = any(k in low for k in plan_keywords)
        plans_online = any(
            k in low
            for k in ["online campus", "arizona online", "ua online", "online student"]
        )
        if "online" in low and has_plan_keyword:
            plans_online = True
        plans_campus = (has_plan_keyword and not plans_online) or any(
            k in low for k in ["on campus", "distance student", "distance program"]
        )

        transfer_process = any(
            k in low
            for k in [
                "transfer process",
                "process for transfer",
                "how to transfer credit",
                "transfer steps",
                "submit transcripts",
            ]
        )
        transfer_applicability = any(
            k in low
            for k in [
                "transfer credit applicability",
                "credit applicability",
                "will my credits transfer",
                "credit apply to degree",
                "transfer apply",
            ]
        )
        transfer_general = "transfer" in low or "transfer credit" in low

        sfwe_topics = {
            "coursework": any(
                k in low
                for k in [
                    "required coursework",
                    "required courses",
                    "curriculum",
                    "coursework",
                    "degree requirements",
                    "courses offered",
                    "semesters offered",
                    "program requirements",
                ]
            ),
            "prereqs": any(
                k in low for k in ["prereq", "prerequisite", "prerequisites"]
            ),
            "plans_online": plans_online,
            "plans_campus": plans_campus,
            "electives": any(
                k in low
                for k in [
                    "technical elective",
                    "technical electives",
                    "elective",
                    "electives",
                    "pre-approved",
                    "preapproved",
                ]
            ),
            "transfer_process": transfer_process,
            "transfer_applicability": transfer_applicability,
            "transfer_general": transfer_general,
            "registration": any(
                k in low
                for k in [
                    "registration",
                    "register",
                    "enroll",
                    "enrollment",
                    "uaccess",
                    "course registration",
                ]
            ),
            "admission": any(
                k in low
                for k in [
                    "major admission",
                    "admission requirements",
                    "change of major",
                    "declare major",
                    "major application",
                ]
            ),
        }

        specific_topic_requested = any(sfwe_topics.values())

        if not mentions_bs_program and re.search(r"\bbs\b", low):
            if specific_topic_requested or mentions_sfwe_keyword:
                mentions_bs_program = True
            elif "software" in low and "engineer" in low:
                mentions_bs_program = True

        if mentions_bs_program or mentions_sfwe_keyword:
            program_url = (
                "https://ece.engineering.arizona.edu/software-engineering-program"
            )
            advising_mail = "sfwe-advising@arizona.edu"

            if sfwe_topics["coursework"]:
                bot_response = Markup(
                    "<b>BS Software Engineering - Required Coursework</b><br>"
                    "• Includes core Software Engineering courses, math/science foundations, general education, and a senior capstone.<br>"
                    "• Course availability varies by term; always check the current schedule and catalog for offerings.<br>"
                    f"See details: <a href='{program_url}' target='_blank' rel='noopener'>Program page</a> | "
                    f"Email advising: <a href='mailto:{advising_mail}'>{advising_mail}</a>"
                );
                return reply(bot_response)
            elif sfwe_topics["prereqs"]:
                bot_response = Markup(
                    "<b>BS Software Engineering - Prerequisites</b><br>"
                    "• Many upper-division SFWE courses require prior CS/SE foundations and math prerequisites.<br>"
                    "• Prerequisites are enforced at registration; verify them in the UA catalog and class schedule.<br>"
                    f"More info: <a href='{program_url}' target='_blank' rel='noopener'>Program page</a> | "
                    f"Contact advising: <a href='mailto:{advising_mail}'>{advising_mail}</a>"
                );
                return reply(bot_response)
            elif sfwe_topics["plans_online"]:
                bot_response = Markup(
                    "<b>BS Software Engineering - 4-Year Planning (Arizona Online)</b><br>"
                    "• Arizona Online plans follow seven-and-a-half-week terms and adjusted course sequencing.<br>"
                    "• Meet advising to map courses to upcoming online offerings and balance workload per term.<br>"
                    f"Start here: <a href='{program_url}' target='_blank' rel='noopener'>Program page</a> | "
                    f"Email advising: <a href='mailto:{advising_mail}'>{advising_mail}</a>"
                );
                return reply(bot_response)
            elif sfwe_topics["plans_campus"]:
                bot_response = Markup(
                    "<b>BS Software Engineering - 4-Year Planning (Main Campus/Distance)</b><br>"
                    "• Typical eight-semester guides balance CS/SE foundations, math/science, and electives.<br>"
                    "• Plans shift with math placement, transfer credit, and catalog year; advising can tailor yours.<br>"
                    f"Planning resources: <a href='{program_url}' target='_blank' rel='noopener'>Program page</a> | "
                    f"Email advising: <a href='mailto:{advising_mail}'>{advising_mail}</a>"
                );
                return reply(bot_response)
            elif sfwe_topics["electives"]:
                bot_response = Markup(
                    "<b>BS Software Engineering - Technical Electives</b><br>"
                    "• Technical electives must be pre-approved and may vary by catalog year.<br>"
                    "• Ask advising for the current approved list and how it fits your plan.<br>"
                    f"Advising: <a href='mailto:{advising_mail}'>{advising_mail}</a> | "
                    f"Program info: <a href='{program_url}' target='_blank' rel='noopener'>Program page</a>"
                );
                return reply(bot_response)
            elif (
                    sfwe_topics["transfer_process"]
                    or sfwe_topics["transfer_applicability"]
                    or sfwe_topics["transfer_general"]
            ):
                bot_response = Markup (
                    "<b>BS Software Engineering - Transfer Credit</b><br>"
                    "• Applicability depends on the official UA evaluation; submit transcripts and review equivalencies in UA tools.<br>"
                    "• Process: send official transcripts, confirm course matches, and partner with SFWE advising to place credits in your plan.<br>"
                    f"Admissions (transfer): <a href='https://admissions.arizona.edu/apply/transfer' target='_blank' rel='noopener'>Link</a> | "
                    f"Advising: <a href='mailto:{advising_mail}'>{advising_mail}</a>"
                );
                return reply(bot_response)
            elif sfwe_topics["registration"]:
                bot_response = Markup(
                    "<b>BS Software Engineering - Course Registration</b><br>"
                    "• Register during your assigned window; clear holds and meet prerequisites first.<br>"
                    "• If you hit errors (requisites/permits/waitlists), contact SFWE advising for options.<br>"
                    f"Program info: <a href='{program_url}' target='_blank' rel='noopener'>Program page</a> | "
                    f"Advising: <a href='mailto:{advising_mail}'>{advising_mail}</a>"
                );
                return reply(bot_response)
            elif sfwe_topics["admission"]:
                bot_response = Markup(
                    "<b>BS Software Engineering - Major Admission</b><br>"
                    "• University admission is through Undergraduate Admissions; program/major placement follows UA policies.<br>"
                    "• For change-of-major or internal transfer to SFWE, meet with advising to review criteria and timing.<br>"
                    f"Undergrad apply: <a href='https://admissions.arizona.edu/apply/freshman' target='_blank' rel='noopener'>Admissions</a> | "
                    f"Advising: <a href='mailto:{advising_mail}'>{advising_mail}</a>"
                );
                return reply(bot_response)
            else:
                bot_response = Markup(
                    "<b>BS Software Engineering Overview</b><br>"
                    "Here's the quick breakdown I can help with:<br>"
                    "• <b>Required coursework & when classes run:</b> Core SFWE sequence plus math/science, general education, and a senior capstone; offerings vary by term, so confirm in the current schedule.<br>"
                    "• <b>Course prerequisites:</b> Upper-division SFWE classes expect CS/SE foundations and math prereqs, all enforced during registration.<br>"
                    "• <b>Major admission:</b> Enter through UA Undergraduate Admissions, then work with SFWE advising for major placement or change-of-major steps.<br>"
                    "• <b>4-year plan (main campus/distance):</b> Standard eight-semester plans adjust for math placement and transfer work; advising will personalize your roadmap.<br>"
                    "• <b>4-year plan (Arizona Online):</b> Online sequencing follows shorter terms, so coordinate with advising to balance courses each session.<br>"
                    "• <b>Technical electives:</b> Choose from the pre-approved list and confirm availability with advising because options vary by catalog year.<br>"
                    "• <b>Transfer credit applicability:</b> UA's official evaluation decides what counts toward SFWE requirements - submit transcripts early.<br>"
                    "• <b>Transfer credit process:</b> Send official transcripts, review UA equivalencies, and meet advising to slot credits into your degree plan.<br>"
                    "• <b>Course registration:</b> Use UAccess during your window, clear holds, and contact advising for permits, requisites, or waitlist help.<br>"
                    f"Program hub: <a href='{program_url}' target='_blank' rel='noopener'>{program_url}</a> | "
                    f"Email advising: <a href='mailto:{advising_mail}'>{advising_mail}</a><br>"
                    "Ask for any item above (e.g., 'BS prerequisites' or 'BS transfer process') if you'd like more detail."
                );
                return reply(bot_response)

        if session["state"] == ChatState.ASKING_ADVISOR_TYPE:
            if any(word in user_input for word in ("undergraduate", "undergrad", "ugrad")):
                response = "There are two advisors for undergraduate Software Engineering Program. Which would you like information for, Alexis or Selim?"
                session["state"] = ChatState.SPECIFIC_ADVISOR_TYPE
            elif any(word in user_input for word in ("graduate", "grad")):
                response = get_grad_advisor_html()
                session["state"] = ChatState.DEFAULT
            else:
                response = "Please specify: Undergraduate or Graduate advising?"

            return reply(response)

        if session["state"] == ChatState.SPECIFIC_ADVISOR_TYPE:
            if "alexis" in user_input:
                response = (
                    'Alexis Vasquez is in the Electrical and Computer Engineering 263<br>'
                    '<a href="tel:5206216171">520.621.6171</a> or '
                    '<a href="mailto:alexisvasquez@arizona.edu">alexisvasquez@arizona.edu</a>. '
                    'You can also visit her <a href="https://ece.engineering.arizona.edu/faculty-staff/staff/alexis-vasquez" '
                    'target="_blank" rel="noopener noreferrer">faculty page</a>.'
                )
                session["state"] = ChatState.DEFAULT
            elif "selim" in user_input:
                response = (
                    'Selim Orbay is in the Electrical and Computer Engineering 261<br>'
                    '<a href="tel:5206212434">520.621.2434</a> or '
                    '<a href="mailto:sao@arizona.edu">sao@arizona.edu</a>. '
                    'You can also visit his <a href="https://ece.engineering.arizona.edu/faculty-staff/staff/selim-orbay" '
                    'target="_blank" rel="noopener noreferrer">faculty page</a>.'
                )
                session["state"] = ChatState.DEFAULT
            else:
                response = "Please specify: Alexis or Selim?"

            return reply(response)

        if session["state"] != ChatState.DEFAULT:
            if any(k in user_input for k in ["career", "careers", "job", "jobs", "advisor", "admissions", "research"]):
                session["state"] = ChatState.DEFAULT

        for field, details in career_data.items():
            if field in user_input:
                response = (
                    f"<b>{field.title()} Careers</b><br>"
                    f"- Roles: {', '.join(details['roles'])}<br>"
                    f"- Typical Salary: {details['salary']}<br>"
                    f"- Explore more: <a href='{details['link']}' target='_blank'>Job Listings & Salaries</a>"
                )
                session["state"] = ChatState.DEFAULT
                return reply(response)

        if bot_response is None and any(term in user_input for term in
            ["ms sfwe", "ms software engineering", "master", "graduate program"]):
            session["state"] = ChatState.ASKING_MS_SFWE
            response = (
                "<b>MS in Software Engineering (University of Arizona)</b><br><br>"
                "What would you like to know about?<br>"
                "• Admission requirements<br>"
                "• Required coursework<br>"
                "• Course prerequisites<br>"
                "• Technical electives<br>"
                "• Specialization tracks<br>"
                "• Thesis vs Non-thesis options<br><br>"
                "Please type one of the above topics."
            )
            return reply(response)


        elif session["state"] == ChatState.ASKING_MS_SFWE:
            # If the user asks doctoral/PhD topics while in the MS flow, hand off to AdmissionsAssistant
            if any(t in user_input for t in phd_terms):
                bot = AdmissionsAssistant(
                    history=session.get("history", []),
                    admissions_flow=session.get("admissions_flow", {}),
                )
                bot.handle_message(raw_input)
                new_state = bot.get_state()
                session["history"] = new_state.get("history", [])
                session["admissions_flow"] = new_state.get("admissions_flow", {})
                # stay in DEFAULT for general UI routing
                session["state"] = ChatState.DEFAULT
                return render_template("index.html", history=session["history"])
            if "admission" in user_input:
                return reply(
                    "<b>MS in Software Engineering — Admission Requirements</b><br><br>"
                    "• Bachelor's degree in Software Engineering, Computer Science, or related field.<br>"
                    "• Minimum GPA of 3.0.<br>"
                    "• GRE optional.<br>"
                    "• Statement of purpose and two recommendation letters.<br>"
                    "• TOEFL/IELTS required for international applicants.<br><br>"
                    "<a href='https://grad.arizona.edu/catalog/programinfo/SWENGMS' target='_blank'>Official Admission Info</a>"
                )
            elif "prerequisite" in user_input:
                return reply(
                    "<b>Course Prerequisites</b><br>"
                    "• Programming in C++, Java, or Python<br>"
                    "• Data structures and algorithms<br>"
                    "• Software design principles<br>"
                    "• Computer systems concepts<br><br>"
                    "Foundation courses may be required for non-CS backgrounds."
                )
            elif "course" in user_input or "coursework" in user_input:
                return reply(
                    "<b>Required Coursework</b><br>"
                    "• SWENG 501 – Software Engineering Principles<br>"
                    "• SWENG 505 – Advanced Software Design<br>"
                    "• SWENG 510 – Software Project Management<br>"
                    "• SWENG 520 – Software Verification & Validation<br>"
                    "• SWENG 530 – Software Architecture & Design Patterns<br>"
                    "• SWENG 540 – Secure Software Development"
                )
            elif "elective" in user_input:
                return reply(
                    "<b>Technical Electives</b><br>"
                    "• Cloud Computing<br>"
                    "• Software Security<br>"
                    "• Agile Software Development<br>"
                    "• Machine Learning for Engineers<br>"
                    "• Data Visualization<br>"
                    "• Embedded Systems<br>"
                    "<br><a href='https://software.engineering.arizona.edu/graduate/courses' target='_blank'>Course Catalog</a>"
                )
            elif "track" in user_input or "specialization" in user_input:
                return reply(
                    "<b>Specialization Tracks</b><br>"
                    "• Embedded & Cyber-Physical Systems<br>"
                    "• AI & Machine Learning Systems<br>"
                    "• Cloud & DevOps Engineering<br>"
                    "• Secure Software Systems<br>"
                    "• Data-Centric Systems"
                )
            elif "thesis" in user_input or "non-thesis" in user_input:
                return reply(
                    "<b>Thesis vs Non-Thesis Options</b><br>"
                    "• Thesis Track: 6 units research and defense.<br>"
                    "• Non-Thesis Track: coursework/capstone.<br>"
                    "Total 30 graduate units."
                )
            else:
                return reply(
                    "Please specify one of the following topics:<br>"
                    "admission requirements, coursework, prerequisites, electives, tracks, or thesis options."
                )

        # ----- High-level routing when we're in DEFAULT state -----
        if session["state"] == ChatState.DEFAULT:
            if "advisor" in user_input:
                session["state"] = ChatState.ASKING_ADVISOR_TYPE
                return reply("Are you looking for Undergraduate or Graduate advisors?")

            elif "admissions" in user_input:
                # ChatGPT, Logic is jumping straight to fallback instead of following through logic, 11/11/25
                bot = AdmissionsAssistant(
                    history=session.get("history", []),
                    admissions_flow=session.get("admissions_flow", {}),
                )
                bot.handle_message(raw_input)  # will start the choose_program stage
                new_state = bot.get_state()
                session["history"] = new_state.get("history", [])
                session["admissions_flow"] = new_state.get("admissions_flow", {})
                session["state"] = ChatState.ASKING_ADMISSIONS_TYPE
                return render_template("index.html", history=session["history"])

            elif "research" in user_input:
                session["state"] = ChatState.ASKING_RESEARCH
                return reply(
                    "Visit: <a href='https://ece.engineering.arizona.edu/research/centers' "
                    "target='_blank'>UA Research Centers</a>"
                )

            elif "careers" in user_input or "jobs" in user_input:
                buttons = []
                for field in career_data.keys():
                    escaped_value = html.escape(field)
                    buttons.append(
                        "<form method='post' style='display:inline;margin-right:6px;'>"
                        f"<input type='hidden' name='message' value=\"{escaped_value}\">"
                        f"<button type='submit'>{html.escape(field.title())}</button></form>"
                    )
                return reply(
                    Markup(
                        "Here are some career fields you can ask me about:<br>"
                        + " ".join(buttons)
                    )
                )

        # ----- Final Fallback for ML (runs no matter what state we're in, unless we already returned) -----
        if response is None or (isinstance(response, str) and not response.strip()):
            # If the user typed a short single word like "Dissertation",
            # turn it into a clearer question for the ML model.
            query_for_ml = raw_input.strip()
            if " " not in query_for_ml and len(query_for_ml) > 4:
                query_for_ml = "Can you explain what a {} is in graduate school?".format(
                    query_for_ml
                )

            # Build a short-term memory window for ML: last turns + current user message
            try:
                recent = get_recent_memory(state_name="default") or []
            except Exception:
                recent = []

            conversation = list(recent) + [
                {"role": "user", "content": query_for_ml},
            ]

            try:
                ai_result = ai_handle_messages(conversation)
            except Exception:
                ai_result = None

            if ai_result and (ai_result.get("answer") or "").strip():
                intent = ai_result.get("intent", "unknown")

                # Make confidence robust even if it's None or missing
                confidence_val = ai_result.get("confidence", 0.0)
                try:
                    confidence = float(confidence_val or 0.0)
                except (TypeError, ValueError):
                    confidence = 0.0

                # --- Guard against bogus "smalltalk_greeting" ---
                # Only accept the greeting if the *current* user input looks like a greeting.
                if intent == "smalltalk_greeting":
                    if not re.search(
                            r"\b(hi|hello|hey|good morning|good afternoon|good evening)\b",
                            raw_input.lower(),
                    ):
                        # Current message isn't a greeting -> bypass the greeting answer.
                        try:
                            plain_answer = ai_answer(query_for_ml)
                        except Exception:
                            plain_answer = None
                        if plain_answer and plain_answer.strip():
                            return reply(plain_answer)
                        # If that also fails, fall through to the normal fallback below.

                base_answer = ai_result["answer"]
                steering = build_steering_suggestion(
                    intent=intent,
                    user_input=raw_input,
                    recent=recent,
                    confidence=confidence,
                )
                full_answer = base_answer + (steering or "")
                return reply(full_answer)

        # If everything above failed, give a simple safety-net response.
        if bot_response is None:
            bot_response = (
                "Sorry, I didn’t catch that. You can ask about advisors, admissions, careers, "
                "or type 'reset' to start over."
            )
        return reply(bot_response)
    return render_template("index.html", history=session["history"])


@app.route("/feedback", methods=["POST"])
def feedback():
    q = request.form.get("q", "")
    a = request.form.get("a", "")
    r = request.form.get("r", "down")
    save_feedback(q, a, r)
    return ("", 204)

#FIXME Remove when done testing!
@app.route("/_debug/memory")
def debug_memory():
    try:
        default_mem = get_recent_memory(state_name="default")
    except Exception:
        default_mem = []
    try:
        adm_mem = get_recent_memory(state_name="admissions")
    except Exception:
        adm_mem = []
    # Render simple HTML so you can check it in a browser
    return render_template(
        "index.html",
        history=[
            ("_debug", f"default memory: {default_mem}"),
            ("_debug", f"admissions memory: {adm_mem}")
        ]
    )



# -------- presentation helper for modern UI --------


@app.template_filter("pretty")
def pretty(s: Optional[str]) -> Markup:
    if not s:
        return Markup("")
    if isinstance(s, Markup):
        return s
    text = str(s)
    if "<" in text and ">" in text:
        maybe_plain = BR_TAG_RE.sub("", text)
        if "<" in maybe_plain and ">" in maybe_plain:
            return Markup(text)
    text = BR_TAG_RE.sub("\n", text)
    text = text.replace("\u0007", "- ")
    return Markup(escape(text))


career_data = {
    "social media platforms": {
        "roles": ["Frontend Engineer", "Backend Engineer", "Mobile App Developer", "Data Engineer"],
        "salary": "USD 1500 – 3500 / month",
        "link": "https://www.glassdoor.com/Job/social-media-software-engineer-jobs-SRCH_KO0,35.htm"
    },
    "cloud computing": {
        "roles": ["Cloud Solutions Architect", "DevOps Engineer", "Cloud Security Specialist"],
        "salary": "USD 1800 – 4500 / month",
        "link": "https://www.indeed.com/q-Cloud-Computing-Engineer-jobs.html"
    },
    "embedded systems": {
        "roles": ["Firmware Engineer", "IoT Developer", "Systems Programmer"],
        "salary": "USD 1200 – 3000 / month",
        "link": "https://www.glassdoor.com/Job/embedded-systems-engineer-jobs-SRCH_KO0,28.htm"
    },
    "artificial intelligence": {
        "roles": ["ML Engineer", "AI Research Scientist", "AI Product Developer"],
        "salary": "USD 2000 – 5000 / month",
        "link": "https://www.indeed.com/q-Artificial-Intelligence-Engineer-jobs.html"
    },
    "automation": {
        "roles": ["Automation Engineer", "RPA Developer", "Test Automation Engineer"],
        "salary": "USD 1300 – 3200 / month",
        "link": "https://www.indeed.com/q-Automation-Engineer-jobs.html"
    },
    "cybersecurity": {
        "roles": ["Security Engineer", "Penetration Tester", "Cybersecurity Analyst"],
        "salary": "USD 1800 – 4500 / month",
        "link": "https://www.indeed.com/q-Cyber-Security-Engineer-jobs.html"
    },
    "machine learning": {
        "roles": ["ML Engineer", "Data Scientist", "NLP Engineer"],
        "salary": "USD 2000 – 4800 / month",
        "link": "https://www.glassdoor.com/Job/machine-learning-engineer-jobs-SRCH_KO0,29.htm"
    },
    "full stack development": {
        "roles": ["Full Stack Engineer", "Software Developer", "Web Applications Engineer"],
        "salary": "USD 1400 – 3800 / month",
        "link": "https://www.indeed.com/q-Full-Stack-Engineer-jobs.html"
    }
}

if __name__ == "__main__":
    app.run(debug=True)
