from markupsafe import Markup

def get_grad_advisor_html() -> Markup:
    """Canonical contact block for the graduate advisor (Jorge Camarillo)."""
    return Markup(
        'Jorge Camarillo is the Graduate Academic Advisor for the Graduate Software Engineering Program. '
        'He is in Electrical and Computer Engineering 266. '
        '<a href="tel:5206210575">520.621.0575</a> or '
        '<a href="mailto:jorgecamarillo@arizona.edu">jorgecamarillo@arizona.edu</a>. '
        'You can also visit his '
        '<a href="https://ece.engineering.arizona.edu/faculty-staff/staff/jorge-camarillo" '
        'target="_blank" rel="noopener noreferrer">faculty page</a>.'
    )

def get_grad_advisor_plain() -> str:
    """Plain-text version for flows that prefer text (admissions)."""
    return (
        "Graduate admissions/advising contact:\n"
        "• Jorge Camarillo (ECE 266) — 520.621.0575, jorgecamarillo@arizona.edu\n"
        "Share your background (MS or PhD interest) and he can help you outline requirements "
        "or connect you with faculty/admissions staff."
    )

# --- FEATURE 1: University of Arizona resource links ---
def get_official_links() -> Markup:
    """Return the HTML block with official UA resource links."""
    return Markup(
        "<b>Official University of Arizona Resources</b><br>"
        "• <a href='https://ece.engineering.arizona.edu/software-engineering-program' target='_blank'>Software Engineering Program</a><br>"
        "• <a href='https://admissions.arizona.edu/' target='_blank'>UA Admissions</a><br>"
        "• <a href='https://grad.arizona.edu/' target='_blank'>Graduate College</a><br>"
        "• <a href='https://career.arizona.edu/' target='_blank'>Career Services</a><br>"
        "• <a href='https://ece.engineering.arizona.edu/research/centers' target='_blank'>Research Centers</a>"
    )

# --- FEATURE 2: Welcome message block ---
def get_welcome_message() -> Markup:
    """Return the welcome message HTML block."""
    return Markup(
        "<b>👋 Welcome to ChatCat!</b><br>"
        "I’m your Software Engineering program assistant.<br>"
        "You can ask about:<br>"
        "• Advisors (Undergraduate or Graduate)<br>"
        "• Admissions information<br>"
        "• Careers & internships<br>"
        "• Research centers<br><br>"
        "Try: <i>“Who are the undergraduate advisors?”</i> or <i>“Graduate admission requirements”</i>."
    )
