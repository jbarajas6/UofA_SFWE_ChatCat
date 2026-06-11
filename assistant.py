from __future__ import annotations

from textwrap import dedent
import re
from typing import List, Tuple, Optional, Dict, Any
from messages import get_grad_advisor_plain
from memory import add_to_memory #ChatGPT, Help Create Short-term memory, 11/11/2025
ASSISTANT_MEMORY_STATE = "admissions" #ChatGPT, Help Create Short-term memory, 11/11/2025




LINKS = {
    "ua_undergrad_apply": "https://admissions.arizona.edu/apply/freshman",
    "ua_transfer": "https://admissions.arizona.edu/apply/transfer",
    "ua_international_ug": "https://admissions.arizona.edu/international",
    "ua_grad_apply": "https://grad.arizona.edu/admissions/apply-now",
    "ua_grad_requirements": "https://grad.arizona.edu/admissions/requirements",
    "ua_grad_deadlines": "https://grad.arizona.edu/admissions/procedures/application-deadlines",
    "ua_grad_international": "https://grad.arizona.edu/admissions/requirements/international-applicants",
    "ua_dual_degree": "https://grad.arizona.edu/catalog/programs",
}

PHD_LINKS = {
    "grad_catalog": "https://grad.arizona.edu/catalog/programs",
    "course_work": "https://infosci.arizona.edu/phd-information/curriculum-courses",
    "PhD_Info": "https://grad.arizona.edu/degree-services/degree-requirements/doctor-philosophy",
    "Advisor_Info": "https://advising.arizona.edu/",
    "research_Info": "https://research.arizona.edu/about/key-research-areas",
    "requirements_Info": "https://grad.arizona.edu/admissions/requirements",
    "Q_exam": "https://philosophy.arizona.edu/phd-philosophy/requirements",
    "funding_overview": "https://grad.arizona.edu/funding/opportunities",
    "funding_Info": "https://grad.arizona.edu/funding",
}


def _fmt(block: str) -> str:
    return dedent(block).strip()


def _normalize_text(text: Optional[str]) -> str:
    if text is None:
        return ""
    text = re.sub(r"\s*<br\s*/?>\s*", "\n", text, flags=re.I)
    return text.strip()


class AdmissionsAssistant:
    """
    Encapsulates the admissions conversation logic and state.

    State schema (stable API for integration):
      state = {
        'history': List[Tuple[str, str]],  # (speaker, text)
        'admissions_flow': {
            'stage': Optional[str],    # None | 'choose_program' | 'bs_details' | 'grad_details'
            'program': Optional[str],  # 'bs' | 'ms' | 'phd'
            'topic': Optional[str],    # e.g., 'deadlines', 'documents', ...
        }
      }

    Public methods intended for a state machine:
      - handle_message(text) -> List[str]: processes input and returns bot replies
      - get_state() / set_state(state): retrieve or replace the current state
      - reset(): clears the conversation and flow
    """

    def __init__(
        self,
        history: Optional[List[Tuple[str, str]]] = None,
        admissions_flow: Optional[Dict[str, Any]] = None,
    ) -> None:
        # Normalize incoming flow dict (may be None or missing keys)
        flow = admissions_flow or {}

        self._state: Dict[str, Any] = {
            "history": list(history or []),
            "admissions_flow": {
                "stage": flow.get("stage"),
                "program": flow.get("program"),
                "topic": flow.get("topic"),
                # new flag we use to signal “let AI take over”
                "handoff": flow.get("handoff", False),
            },
        }

    # -------------------- state API --------------------
    def get_state(self) -> Dict[str, Any]:
        return {
            "history": list(self._state.get("history", [])),
            "admissions_flow": dict(self._state.get("admissions_flow", {})),
        }

    def set_state(self, state: Dict[str, Any]) -> None:
        history = state.get("history") or []
        flow = state.get("admissions_flow") or {}
        self._state["history"] = list(history)
        self._state["admissions_flow"] = {
            "stage": flow.get("stage"),
            "program": flow.get("program"),
            "topic": flow.get("topic"),
            "handoff": flow.get("handoff", False),
        }

    def reset(self) -> None:
        self._state["history"] = []
        self._state["admissions_flow"] = {
            "stage": None,
            "program": None,
            "topic": None,
        }

    # -------------------- message helpers --------------------
    def say(self, text: str) -> None:
        #ChatGPT, Add Short-term memory to CatChat, 11/11/2025.
        normalized = _normalize_text(text)
        self._state["history"].append(("ChatCat", normalized))
        # Mirror assistant turn to short-term memory
        try:
            add_to_memory("assistant", normalized, state_name=ASSISTANT_MEMORY_STATE)
        except Exception:
            # Avoid crashing if session/memory is unavailable in some contexts (e.g., CLI tests)
            pass
        #EDITING FOR NOW self._state["history"].append(("ChatCat", _normalize_text(text)))

    def user(self, text: str) -> None:
        # ChatGPT, Add Short-term memory to CatChat, 11/11/2025.
        normalized = _normalize_text(text)
        self._state["history"].append(("You", normalized))
        # Mirror user turn to short-term memory
        try:
            add_to_memory("user", normalized, state_name=ASSISTANT_MEMORY_STATE)
        except Exception:
            pass
        #EDITING FOR NOW self._state["history"].append(("You", _normalize_text(text)))


    # -------------------- intent helpers --------------------
    _PHD_TOPIC_TERMS = [
        "qualifying exam",
        "qualifier",
        "dissertation",
        "research",
        "faculty",
        "advisor",
        "advisors",
        "coursework",
        "course work",
        "minor",
        "prerequisite",
        "prerequisites",
        "elective",
        "electives",
        "funding",
        "assistantship",
        "assistantships",
        "fellowship",
        "fellowships",
        "scholarship",
        "scholarships",
    ]

    def _grad_topic_from_low(self, low: str) -> Optional[str]:
        low = low or ""
        if "deadline" in low:
            return "deadlines"
        if "eligibility" in low or "criteria" in low:
            return "eligibility"
        if "document" in low or "requirement" in low:
            return "documents"
        if "international" in low:
            return "international"
        if "dual" in low:
            return "dual"
        return None

    def _mentions_phd_topic(self, low: str) -> bool:
        low = low or ""
        return any(term in low for term in self._PHD_TOPIC_TERMS)

    def _prompt_phd_clarification(self) -> None:
        flow = self._state["admissions_flow"]
        flow["topic"] = "clarify_grad_vs_phd"
        self.say(
            "That sounds like a research/advisor topic. Do you want to switch to the PhD menu, "
            "or keep going with MS admissions (deadlines, eligibility, documents, international, dual-degree)? "
            "Reply 'phd menu' to switch, 'admissions' to stay, or type 'cancel' to exit."
        )

    def _wants_to_switch_phd(self, low: str) -> bool:
        low = (low or "").strip()

        if low == "phd":
            return True

        if "phd" in low and len(low.split()) <= 5:
            return True

        triggers = ["phd menu","switch to phd","phd program","phd admissions","phd info","phd details","phd topics",]
        return any(trigger in low for trigger in triggers)



    def _wants_to_stay_grad(self, low: str) -> bool:
        low = low or ""
        tokens = [
            "admissions",
            "stay",
            "continue",
            "keep",
            "ms",
            "masters",
            "master's",
            "graduate",
        ]
        return any(token in low for token in tokens)

    def _admissions_advisor_info(self, program: str) -> None:
        program = (program or "").lower()
        if program == "bs":
            self.say(
                _fmt(
                    """
                Undergraduate admissions/advising support:
                \u0007 Alexis Vasquez (ECE 263) — 520.621.6171, alexisvasquez@arizona.edu
                \u0007 Selim Orbay (ECE 261) — 520.621.2434, sao@arizona.edu
                Email sfwe-advising@arizona.edu to discuss BS admission steps, transfer credit, or planning.
            """
                )
            )
        else:
            self.say(_fmt(get_grad_advisor_plain()))

    # -------------------- flow steps --------------------
    def _start_admissions(self) -> None:
        self.say(
            "I can help with admissions. Which program are you asking about: BS, MS, or PhD?"
        )
        self._state["admissions_flow"]["stage"] = "choose_program"

        # ------------------PhD------------------------------------
    def _topic_key(self, low: str) -> str:
        # normalize user wording to our topic keys
        pairs = [
            ("admission requirements", ["admission requirement", "admissions requirement"]),
            ("required course work", ["coursework", "course work"]),
            ("potential minor options", ["minor", "minors"]),
            ("required prerequisites", ["prerequisite", "prerequisites"]),
            ("technical electives", ["electives", "technical elective", "tech electives"]),
            ("research & advisors", ["research", "advisor", "advisors"]),
            ("qualifying exam", ["qualifying exams", "qualifying", "exam"]),
            ("dissertation", ["dissertation"]),
            ("funding", ["funding", "opportunities", "opportunity"]),
        ]
        for key, triggers in pairs:
            if any(t in low for t in triggers):
                return key
        return ""

    def _phd_menu(self) -> None:
        self.say(_fmt("""
                PhD Software Engineering — what would you like to know?
                • Admission requirements
                • Required course work
                • Potential minor options
                • Required course prerequisites
                • Possible technical electives
                • Guidance on research focus areas and faculty advisors
                • Qualifying exam requirements
                • Dissertation requirements
                • Funding opportunities
        """))

    def _phd_sfwe_details(self, topic_key: str) -> None:
        t = (topic_key or "").lower()
        if t == "admission requirements":
            self.say(_fmt(f"""
                    PhD admissions requirements: 
                    • Cumulative GPA of 3.0
                    • Transcripts
                    • 3 recommendation letters
                    • English proficiency (if applicable)
                    • See other requirements: {PHD_LINKS["requirements_Info"]}
            """))
        elif t == "required course work":
            self.say(_fmt(f"""
                    Required course work is defined by the PhD major and your advisory committee but it may consist of:
                    • 36 credits of major coursework
                    • 9 to 15 credits of minor coursework
                    • 18 dissertation credits
                    • Start with the Graduate Catalog to locate the exact program page: {PHD_LINKS["grad_catalog"]}
                    • See furthur course work information: {PHD_LINKS["course_work"]} • {PHD_LINKS["PhD_Info"]}
            """))
        elif t == "potential minor options":
            self.say(_fmt(f"""
                    The University of Arizona offers a variety of potential minor options for PhD students. Each minor is a minimum of 9 units and must be approved by the student's major professor.  
                    Students are encouraged to consult with their faculty advisors to explore the full range of minor options available. 
                    • More minor information: {PHD_LINKS["PhD_Info"]}
            """))
        elif t == "required prerequisites":
            self.say(_fmt(f"""
                    Prerequisites depend on your background and the research area; your advisor may assign advanced courses if needed.
                    Typically you need to complete the following prerequisites:
                    • Complete a minimum of 36 units in the major subject and 9 units in the minor subject.
                    • Dissertation
                    • Maintain a cumulative GPA of 3.0

                    Each program may have specific additional requirements so:
                    • Check program-specific expectations in: {PHD_LINKS["grad_catalog"]} 
                    and PhD Page: {PHD_LINKS["PhD_Info"]}
            """))
        elif t == "technical electives":
            self.say(_fmt(f"""
                    Technical electives are selected with your advisor to support your research focus (e.g., advanced SE, ML/AI, systems,
                    embedded, verification, cybersecurity). Confirm eligible course lists with your advisor.
                    • See program guidance: {PHD_LINKS["grad_catalog"]} • Contact Advisor: {PHD_LINKS["Advisor_Info"]}
            """))
        elif t == "research & advisors":
            self.say(_fmt(f"""
                    The University of Arizona offers a diverse range of PhD research focus areas, reflecting its commitment to interdisciplinary collaboration and innovation. 
                    Scan recent faculty publications and center pages, then email potential advisors with a concise research fit to get more information.
                    • Research/Partnership: {PHD_LINKS["research_Info"]}
                    • Advisor Page: {PHD_LINKS["Advisor_Info"]}
            """))
        elif t == "qualifying exam":
            self.say(_fmt(f"""
                    A qualifying examination or diagnostic evaluation may be required to demonstrate acceptability to pursue a doctorate as well as to determine areas of study where further course work is necessary. 
                    • See Qualifying exam information here: {PHD_LINKS["Q_exam"]}
            """))
        elif t == "dissertation":
            self.say(_fmt(f"""
                    The structure of the dissertation varies depending upon the discipline and the program offering the degree. See the program's handbook for the details of how your program structures their dissertation requirement. A common measure of quality of the dissertation is whether it is "publishable" in disciplinarily-recognized publication venues.
                    • Dissertation Info: {PHD_LINKS["PhD_Info"]}
            """))
        elif t == "funding":
            self.say(_fmt(f"""
                    The University of Arizona offers a variety of funding opportunities for PhD students, including scholarships, fellowships, awards for dissertation research and travel, and more. These opportunities are designed to support graduate students in their research and academic pursuits. 
                    • Funding Opportunities: {PHD_LINKS["funding_overview"]}
                    • Other Opportunities: {PHD_LINKS["funding_Info"]}
            """))
        else:
            self._phd_menu()

    def _bs_intro(self) -> None:
        self.say(
            _fmt(
                f"""
            BS application (general steps):

            1) Submit the UA undergraduate application
            2) Provide high school transcripts
            3) ACT/SAT if available (optional for many cases)
            4) Pay the fee

            What do you want next: deadlines; required documents; international admissions;
            transfer credit; or dual-degree options?

            Undergrad apply: {LINKS['ua_undergrad_apply']}
        """
            )
        )
        self._state["admissions_flow"]["stage"] = "bs_details"

    def _grad_intro(self, program: str) -> None:

        base = f"""
            {program.upper()} application (general steps):

            1) Apply via GradApp
            2) Statement of purpose and CV/resume
            3) Transcripts and three recommendation letters
            4) Proof of English proficiency for international applicants

            What do you want next: deadlines; eligibility criteria; required documents;
            international admissions; or dual-degree options?

            Apply: {LINKS['ua_grad_apply']}  •  Requirements: {LINKS['ua_grad_requirements']}
        """

        # Only show the PhD-specific tip when we are actually in the PhD program.
        extra = ""
        if program.lower() == "phd":
            extra = """
            Tip: For other PhD-specific details (prereqs, electives, research/advisors,
            qualifying exam, dissertation, funding), type "phd menu" to see the PhD topics.
            """

        self.say(_fmt(base + extra))
        self._state["admissions_flow"]["stage"] = "grad_details"


    def _prompt_bs_menu(self) -> None:
        self.say(
            "What do you want next: deadlines; required documents; eligibility; international; transfer; or dual-degree options?"
        )

    def _prompt_grad_menu(self) -> None:
        self.say(
            "What do you want next: deadlines; eligibility; required documents; international; or dual-degree options?"
        )

    def _bs_details(self, topic: Optional[str]) -> None:
        t = (topic or "").lower()
        if t == "deadlines":
            self.say(
                _fmt(
                    f"""
                Undergraduate deadlines vary by term (Fall/Spring) and residency.
                Start here and pick your term:
                {LINKS['ua_undergrad_apply']}
            """
                )
            )
            self._prompt_bs_menu()
        elif t in {"documents", "required documents", "requirements"}:
            self.say(
                _fmt(
                    f"""
                Typical BS documents: transcripts; optional test scores; any program forms UA requests during the app.
                Undergrad apply: {LINKS['ua_undergrad_apply']}
            """
                )
            )
            self._prompt_bs_menu()
        elif t in {"eligibility", "eligibility criteria"}:
            self.say(
                _fmt(
                    f"""
                Undergraduate eligibility (high level):
                • High school diploma or equivalent (GED)
                • Official transcripts
                • Program-specific expectations (e.g., GPA and prerequisites vary by major)
                • Test scores (ACT/SAT) optional in many cases

                See current undergrad guidance and start your application here:
                {LINKS['ua_undergrad_apply']}
            """
                )
            )
            self._prompt_bs_menu()
        elif t == "international":
            self.say(
                _fmt(
                    f"""
                International undergrads generally need English proficiency and financial documentation.
                Info: {LINKS['ua_international_ug']}
            """
                )
            )
            self._prompt_bs_menu()
        elif t == "transfer":
            self.say(
                _fmt(
                    f"""
                Transfer applicants: submit official college transcripts; check course transferability; watch term deadlines.
                Transfer info: {LINKS['ua_transfer']}
            """
                )
            )
            self._prompt_bs_menu()
        elif t in {"dual", "dual-degree", "dual degree"}:
            self.say(
                _fmt(
                    f"""
                Dual-degree options are mostly at the graduate level. BS students usually combine minors/certificates.
                Start at undergrad advising: {LINKS['ua_undergrad_apply']}
            """
                )
            )
            self._prompt_bs_menu()
        else:
            # Unrecognized topic – just show the menu once
            self._prompt_bs_menu()

    def _grad_details(self, topic: Optional[str]) -> None:
        t = (topic or "").lower()
        if t == "deadlines":
            self.say(
                _fmt(
                    f"""
                Graduate deadlines are program-specific; there are priority dates and firm cutoffs.
                Deadlines: {LINKS['ua_grad_deadlines']}
            """
                )
            )
            self._prompt_grad_menu()
        elif t in {"eligibility", "eligibility criteria"}:
            self.say(
                _fmt(
                    f"""
                Eligibility is program-specific; generally a relevant accredited degree and solid academic background are expected.
                Program requirements: {LINKS['ua_grad_requirements']}
            """
                )
            )
            self._prompt_grad_menu()
        elif t in {"documents", "required documents", "requirements"}:
            self.say(
                _fmt(
                    f"""
                Typical MS/PhD documents:
                • Transcripts
                • Statement of purpose
                • CV / résumé
                • Three recommendation letters
                • English proficiency for international students

                Details: {LINKS['ua_grad_requirements']}
            """
                )
            )
            self._prompt_grad_menu()
        elif t == "international":
            self.say(
                _fmt(
                    f"""
                International graduates must submit valid English proficiency scores and financial documentation.
                Info: {LINKS['ua_grad_international']}
            """
                )
            )
            self._prompt_grad_menu()
        elif t in {"dual", "dual-degree", "dual degree"}:
            self.say(
                _fmt(
                    f"""
                UA offers some dual/combined graduate programs (e.g., MS + MBA) but availability is program-specific.
                Browse programs: {LINKS['ua_dual_degree']}
            """
                )
            )
            self._prompt_grad_menu()
        else:
            # Unrecognized topic – just show the menu once
            self._prompt_grad_menu()

    # -------------------- main handler --------------------
    def handle_message(self, raw_text: str) -> List[str]:
        """
        Main state machine for admissions conversations.
        """
        text = (raw_text or "").strip()
        low = text.lower()
        self.user(text)

        flow = self._state["admissions_flow"]
        # reset handoff for each new incoming message
        flow["handoff"] = False

        stage = flow.get("stage")
        program = flow.get("program")

        # Common tokens for detecting programs
        bs_tokens = ["bs", "b.s.", "bachelors", "bachelor's"]
        ms_tokens = ["ms", "m.s.", "masters", "master's"]
        phd_tokens = ["phd", "ph.d", "doctoral", "doctorate"]

        # ----------------- Global commands -----------------
        # Soft-escape commands: leave the admissions flow without clearing history
        escape_cmds = {
            "exit",
            "cancel",
            "back",
            "menu",
            "main",
            "main menu",
            "stop",
            "quit",
            "home",
        }
        if low in escape_cmds or low.strip("/") in escape_cmds:
            flow["stage"] = None
            flow["program"] = None
            flow["topic"] = None
            self.say(
                "Okay, returning to the main menu. You can ask about careers, advisors, admissions, or research centers."
            )
            return [self._state["history"][-1][1]]

        # Clear command – wipes admissions state but keeps conversation history
        if low in {"reset", "clear", "/reset"}:
            self.reset()
            self.say("Conversation cleared. Ask me about admissions (BS; MS; PhD).")
            return [self._state["history"][-1][1]]

        # --- Explicit request to hand off to the general AI ---
        if "ask ai" in low or "let the ai" in low or "general question" in low:
            flow["handoff"] = True
            self.say(
                "No problem — I’ll let the general ChatCat assistant handle this question so you’re not limited to the admissions menus."
            )
            return [self._state["history"][-1][1]]


        # ----------------- Starting flow -----------------
        # Kick off the flow on first mention of admissions
        if ("admission" in low or "apply" in low) and stage is None:
            self._start_admissions()
            return [self._state["history"][-1][1]]

        # If no flow yet but the user names a program directly
        if stage is None:
            if any(tok in low for tok in bs_tokens):
                flow["program"] = "bs"
                flow["stage"] = "bs_details"
                self._bs_intro()
                return [self._state["history"][-1][1]]
            if any(tok in low for tok in ms_tokens):
                flow["program"] = "ms"
                flow["stage"] = "grad_details"
                self._grad_intro("ms")
                return [self._state["history"][-1][1]]
            if any(tok in low for tok in phd_tokens):
                flow["program"] = "phd"
                flow["stage"] = "grad_details"
                self._grad_intro("phd")
                return [self._state["history"][-1][1]]

        # ----------------- choose_program stage -----------------
        if stage == "choose_program":
            if any(tok in low for tok in bs_tokens):
                flow["program"] = "bs"
                flow["stage"] = "bs_details"
                self._bs_intro()
            elif any(tok in low for tok in ms_tokens):
                flow["program"] = "ms"
                flow["stage"] = "grad_details"
                self._grad_intro("ms")
            elif any(tok in low for tok in phd_tokens):
                flow["program"] = "phd"
                flow["stage"] = "grad_details"
                self._grad_intro("phd")
            else:
                self.say("Please specify one program: BS; MS; or PhD.")
            return [self._state["history"][-1][1]]

        # ----------------- BS details -----------------
        if stage == "bs_details":
            # 0) Allow switching from BS to MS or PhD inside the flow
            if any(tok in low for tok in ms_tokens):
                flow["program"] = "ms"
                flow["topic"] = None
                flow["handoff"] = False
                self._grad_intro("ms")
                return [self._state["history"][-1][1]]

            if any(tok in low for tok in phd_tokens):
                flow["program"] = "phd"
                flow["topic"] = None
                flow["handoff"] = False
                self._grad_intro("phd")
                return [self._state["history"][-1][1]]

            # BS advisor info (user must ask explicitly)
            if any(
                phrase in low
                for phrase in [
                    "advisor for",
                    "advisors for",
                    "advisor info",
                    "faculty advisor",
                    "faculty list",
                    "undergraduate advisor",
                ]
            ):
                self._admissions_advisor_info("bs")
                self._prompt_bs_menu()
                return [self._state["history"][-1][1]]

            topic = (
                "deadlines"
                if "deadline" in low
                else (
                    "eligibility"
                    if ("eligibility" in low or "criteria" in low)
                    else (
                        "documents"
                        if ("document" in low or "requirement" in low)
                        else (
                            "international"
                            if "international" in low
                            else "transfer"
                            if "transfer" in low
                            else "dual"
                            if "dual" in low
                            else None
                        )
                    )
                )
            )

            if topic is None:
                # no matching admissions topic → suggest ML handoff
                flow["handoff"] = True

            self._bs_details(topic)
            return [self._state["history"][-1][1]]


        # ----------------- Grad (MS / PhD) details -----------------
        if stage == "grad_details":
            low = (text or "").lower()
            program = flow.get("program")
            grad_topic = self._grad_topic_from_low(low)

            # 0) Allow switching from MS/PhD to BS while in grad details
            if any(tok in low for tok in bs_tokens):
                flow["program"] = "bs"
                flow["topic"] = None
                flow["handoff"] = False
                self._bs_intro()
                return [self._state["history"][-1][1]]

            # 1) Switch from PhD back to MS when user says masters/MS
            if program == "phd" and any(tok in low for tok in ms_tokens):
                flow["program"] = "ms"
                flow["topic"] = None
                flow["handoff"] = False
                self._grad_intro("ms")
                return [self._state["history"][-1][1]]

            # 2) If the user clearly wants PhD, switch programs first
            if self._wants_to_switch_phd(low):
                flow["program"] = "phd"
                flow["topic"] = None
                flow["handoff"] = False
                # 'phd menu' will be handled below; otherwise give PhD intro
                if "menu" in low or "topics" in low:
                    self._phd_menu()
                else:
                    self._grad_intro("phd")
                return [self._state["history"][-1][1]]

            # 3) Explicit PhD menu (even if currently on MS)
            if any(
                phrase in low
                for phrase in [
                    "phd menu",
                    "show phd menu",
                    "show phd topics",
                    "phd topics",
                ]
            ):
                flow["program"] = "phd"
                flow["topic"] = None
                flow["handoff"] = False
                self._phd_menu()
                return [self._state["history"][-1][1]]

            if program == "phd":
                # 4a) First handle generic grad admissions topics
                if grad_topic:
                    self._grad_details(grad_topic)
                else:
                    # 4b) Then look for PhD-specific topics
                    key = self._topic_key(low)
                    if key:
                        self._phd_sfwe_details(key)
                    else:
                        # 4c) Nothing matched → hand off to general AI
                        flow["handoff"] = True
                        self.say(
                            "That goes beyond my built-in PhD admissions menus. "
                            "I’ll let the general ChatCat assistant try to help with this question."
                        )
                return [self._state["history"][-1][1]]


            # 5) Explicit advisor intent on MS/generic grad (avoid auto-trigger)
            if program != "phd" and any(
                phrase in low
                for phrase in [
                    "advisor for",
                    "advisor info",
                    "advisors for",
                    "faculty advisor",
                    "faculty list",
                    "graduate advisor",
                ]
            ):
                flow["topic"] = None
                self._admissions_advisor_info(program or "ms")
                self._prompt_grad_menu()
                return [self._state["history"][-1][1]]

            # 6) If we previously asked MS vs PhD clarification
            if flow.get("topic") == "clarify_grad_vs_phd":
                if self._wants_to_switch_phd(low):
                    flow["topic"] = None
                    flow["program"] = "phd"
                    flow["handoff"] = False
                    self._grad_intro("phd")
                    return [self._state["history"][-1][1]]

                if any(word in low for word in ["advisor", "advisors", "faculty"]):
                    flow["topic"] = None
                    self._admissions_advisor_info(program or "ms")
                    self._prompt_grad_menu()
                    return [self._state["history"][-1][1]]

                if grad_topic or self._wants_to_stay_grad(low):
                    flow["topic"] = None
                else:
                    self.say(
                        "Please let me know: type 'phd menu' to switch to PhD research/advisor topics, "
                        "or say 'admissions' (deadlines, eligibility, documents, international, dual-degree) to stay on MS info."
                    )
                    return [self._state["history"][-1][1]]

            # 7) If the text smells like a PhD topic while we're on MS, ask for clarification
            if program != "phd" and self._mentions_phd_topic(low):
                self._prompt_phd_clarification()
                return [self._state["history"][-1][1]]

            # 8) At this point, we are on MS (or generic grad) and may or may not have a grad_topic
            if grad_topic is None:
                # grad menu can't classify this → ask AI to help
                flow["handoff"] = True

            self._grad_details(grad_topic)
            return [self._state["history"][-1][1]]

        # ----------------- Fallbacks when not in a flow -----------------
        if "bs program" in low:
            self.say(
                "The BS Software Engineering program requires core CS/SE courses, electives, and a capstone."
            )
        elif "admission" in low or "apply" in low:
            self._start_admissions()
        else:
            flow["handoff"] = True
            self.say(
                "Sorry, I don't know that yet from the admissions guide. I can have the AI assistant take a try, "
                "or you can ask about: admissions; BS; MS; or PhD."
            )

        return [self._state["history"][-1][1]]

def admissions_step(state: Optional[Dict[str, Any]], message: str):
    """
    Functional adapter for state-machine integrations.

    Args:
      state: existing conversation state dict (or None for new)
      message: user input string

    Returns:
      (new_state, replies): tuple with updated state dict and list of bot reply texts
    """
    bot = AdmissionsAssistant()
    if state:
        bot.set_state(state)
    replies = bot.handle_message(message)
    return bot.get_state(), replies


__all__ = ["AdmissionsAssistant", "LINKS", "admissions_step"]
