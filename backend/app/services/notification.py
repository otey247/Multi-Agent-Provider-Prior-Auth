"""Notification letter generation for prior authorization decisions.

Produces plain-text letters from templates and PDF versions via fpdf2.
Authorization number format: PA-YYYYMMDD-XXXXX (monotonic counter).
"""

import base64
import io
import threading
from datetime import date, timedelta

from fpdf import FPDF, XPos, YPos

_counter_lock = threading.Lock()
_counter = 0


def generate_authorization_number() -> str:
    """Generate a unique PA authorization number: PA-YYYYMMDD-XXXXX."""
    global _counter
    with _counter_lock:
        _counter += 1
        seq = _counter
    return f"PA-{date.today().strftime('%Y%m%d')}-{seq:05d}"


_DISCLAIMER_HEADER = """\
AI-ASSISTED DRAFT - PROVIDER REVIEW REQUIRED
Evidence matching reflects Medicare LCDs/NCDs only. Commercial and Medicare
Advantage plan requirements may differ. Human clinical review required before submission."""


def generate_submission_ready_letter(
    authorization_number: str,
    patient_name: str,
    patient_dob: str,
    provider_name: str,
    provider_npi: str,
    procedure_codes: list[str],
    diagnosis_codes: list[str],
    summary: str,
    insurance_id: str = "",
    policy_references: list[str] | None = None,
    confidence: float = 0,
    confidence_level: str = "",
    clinical_rationale: str = "",
    coverage_criteria_met: list[str] | None = None,
    documentation_gaps: list[dict] | None = None,
    was_overridden: bool = False,
    override_rationale: str = "",
    override_reviewer: str = "",
    original_recommendation: str = "",
) -> dict:
    """Generate a SUBMISSION READY notification for the provider.

    Returns dict matching NotificationLetter schema.
    Auth reference validity: today -> today + 90 days.
    """
    today = date.today()
    expiration = today + timedelta(days=90)

    insurance_line = f"\n  Insurance ID: {insurance_id}" if insurance_id else ""
    policy_section = ""
    if policy_references:
        refs = "\n".join(f"  - {ref}" for ref in policy_references)
        policy_section = f"\n\nPAYER POLICY REFERENCE:\n{refs}"

    confidence_section = ""
    if confidence_level:
        confidence_section = f"\n\nSUBMISSION CONFIDENCE: {confidence_level} ({int(confidence * 100)}%)"

    criteria_section = ""
    if coverage_criteria_met:
        items = "\n".join(f"  - {c}" for c in coverage_criteria_met)
        criteria_section = f"\n\nPAYER REQUIREMENTS MET:\n{items}"

    rationale_section = ""
    if clinical_rationale:
        rationale_section = f"\n\nCLINICAL EVIDENCE SUMMARY:\n{clinical_rationale}"

    gaps_section = ""
    if documentation_gaps:
        items = []
        for gap in documentation_gaps:
            what = gap.get("what", "") or gap.get("description", "")
            critical = gap.get("critical", False)
            # Critical gaps are omitted for ready-to-submit unless staff
            # explicitly overrode the assessment to ready.
            if critical and not was_overridden:
                continue
            label = "REQUIRED" if critical else "RECOMMENDED"
            items.append(f"  - [{label}] {what}")
        if items:
            gaps_section = "\n\nDOCUMENTATION NOTES:\n" + "\n".join(items)

    override_section = ""
    if was_overridden:
        override_section = f"""\n\n======================================================
STAFF OVERRIDE NOTICE
======================================================
This assessment was OVERRIDDEN by {override_reviewer}.
Original AI Assessment: {original_recommendation.replace('_', ' ').upper()}
Staff Decision: READY TO SUBMIT

Override Rationale:
{override_rationale}"""

    body = f"""{_DISCLAIMER_HEADER}

======================================================
PRIOR AUTHORIZATION - READY FOR SUBMISSION
======================================================

Reference Number: {authorization_number}
Date: {today.isoformat()}

STATUS: ** READY TO SUBMIT **{confidence_section}

Dear {provider_name} (NPI: {provider_npi}),

The prior authorization preparation for the following services has been
completed. The request package meets payer documentation requirements
and is ready for submission.

PATIENT INFORMATION:
  Name: {patient_name}
  Date of Birth: {patient_dob}{insurance_line}

REQUESTED SERVICES:
  Procedure Code(s): {', '.join(procedure_codes)}
  Diagnosis Code(s): {', '.join(diagnosis_codes)}

REFERENCE VALIDITY:
  Prepared Date:    {today.isoformat()}
  Reference Expiry: {expiration.isoformat()}{policy_section}

CLINICAL SUMMARY:
{summary}{rationale_section}{criteria_section}{gaps_section}{override_section}

NEXT STEPS:
Please submit this prior authorization request to the payer through the
appropriate channel (portal, clearinghouse, fax, or phone). Reference
number {authorization_number} should be included in all correspondence.

This assessment does not guarantee payer approval. Final authorization
is subject to payer review.

Sincerely,
Prior Authorization Operations"""

    return {
        "authorization_number": authorization_number,
        "letter_type": "submission_ready",
        "effective_date": today.isoformat(),
        "expiration_date": expiration.isoformat(),
        "patient_name": patient_name,
        "provider_name": provider_name,
        "body_text": body,
        "appeal_rights": None,
        "documentation_deadline": None,
    }


# Backward-compatible alias
def generate_approval_letter(*args, **kwargs) -> dict:
    """Legacy alias for generate_submission_ready_letter."""
    return generate_submission_ready_letter(*args, **kwargs)


def generate_needs_documentation_letter(
    authorization_number: str,
    patient_name: str,
    patient_dob: str,
    provider_name: str,
    provider_npi: str,
    procedure_codes: list[str],
    diagnosis_codes: list[str],
    missing_documentation: list[str],
    documentation_gaps: list[dict],
    summary: str,
    insurance_id: str = "",
    policy_references: list[str] | None = None,
    confidence: float = 0,
    confidence_level: str = "",
    clinical_rationale: str = "",
    coverage_criteria_met: list[str] | None = None,
    coverage_criteria_not_met: list[str] | None = None,
    was_overridden: bool = False,
    override_rationale: str = "",
    override_reviewer: str = "",
    original_recommendation: str = "",
) -> dict:
    """Generate a NEEDS DOCUMENTATION provider work item letter.

    Returns dict matching NotificationLetter schema.
    Documentation action deadline: today + 30 days.
    """
    today = date.today()
    deadline = today + timedelta(days=30)

    # Build missing info section from structured documentation_gaps only
    # (missing_documentation from synthesis duplicates the same items)
    missing_items = []
    for gap in documentation_gaps:
        what = gap.get("what", "") or gap.get("description", "")
        request_text = gap.get("request", "")
        critical = gap.get("critical", False)
        label = "REQUIRED" if critical else "Recommended"
        missing_items.append(f"  - [{label}] {what}")
        if request_text:
            missing_items.append(f"    Action: {request_text}")

    missing_section = "\n".join(missing_items) if missing_items else "  - Additional clinical documentation"

    insurance_line = f"\n  Insurance ID: {insurance_id}" if insurance_id else ""
    policy_section = ""
    if policy_references:
        refs = "\n".join(f"  - {ref}" for ref in policy_references)
        policy_section = f"\n\nPAYER POLICY REFERENCE:\n{refs}"

    confidence_section = ""
    if confidence_level:
        confidence_section = f"\n\nASSESSMENT CONFIDENCE: {confidence_level} ({int(confidence * 100)}%)"

    rationale_section = ""
    if clinical_rationale:
        rationale_section = f"\n\nCLINICAL EVIDENCE SUMMARY:\n{clinical_rationale}"

    criteria_met_section = ""
    if coverage_criteria_met:
        items = "\n".join(f"  - {c}" for c in coverage_criteria_met)
        criteria_met_section = f"\n\nPAYER REQUIREMENTS ALREADY MET:\n{items}"

    criteria_not_met_section = ""
    if coverage_criteria_not_met:
        items = "\n".join(f"  - {c}" for c in coverage_criteria_not_met)
        criteria_not_met_section = f"\n\nPAYER REQUIREMENTS NOT YET MET:\n{items}"

    appeal_rights = (
        f"If you believe the required documentation is already on file or have "
        f"questions about these requirements, please contact the prior authorization "
        f"team and reference number {authorization_number}. "
        f"Documentation action deadline: {deadline.isoformat()}."
    )

    override_section = ""
    if was_overridden:
        override_section = f"""\n\n======================================================
STAFF OVERRIDE NOTICE
======================================================
This assessment was OVERRIDDEN by {override_reviewer}.
Original AI Assessment: {original_recommendation.replace('_', ' ').upper()}
Staff Decision: NEEDS DOCUMENTATION REVIEW

Override Rationale:
{override_rationale}"""

    body = f"""{_DISCLAIMER_HEADER}

======================================================
PRIOR AUTHORIZATION - DOCUMENTATION ACTION REQUIRED
======================================================

Reference Number: {authorization_number}
Date: {today.isoformat()}

STATUS: ** ADDITIONAL DOCUMENTATION NEEDED **{confidence_section}

Dear {provider_name} (NPI: {provider_npi}),

The prior authorization request for the following services requires
additional documentation before it can be submitted. Please review
the items listed below and provide or confirm the missing evidence.

PATIENT INFORMATION:
  Name: {patient_name}
  Date of Birth: {patient_dob}{insurance_line}

REQUESTED SERVICES:
  Procedure Code(s): {', '.join(procedure_codes)}
  Diagnosis Code(s): {', '.join(diagnosis_codes)}{policy_section}

CLINICAL SUMMARY:
{summary}{rationale_section}{criteria_met_section}{criteria_not_met_section}{override_section}

DOCUMENTATION ACTION REQUIRED:
{missing_section}

ACTION DEADLINE: {deadline.isoformat()}
Please provide or confirm the above documentation by this date to
avoid delays in patient care.

CONTACT INFORMATION:
{appeal_rights}

Sincerely,
Prior Authorization Operations"""

    return {
        "authorization_number": authorization_number,
        "letter_type": "needs_documentation",
        "effective_date": today.isoformat(),
        "expiration_date": None,
        "patient_name": patient_name,
        "provider_name": provider_name,
        "body_text": body,
        "appeal_rights": appeal_rights,
        "documentation_deadline": deadline.isoformat(),
    }


# Backward-compatible alias
def generate_pend_letter(*args, **kwargs) -> dict:
    """Legacy alias for generate_needs_documentation_letter."""
    return generate_needs_documentation_letter(*args, **kwargs)


# ---------------------------------------------------------------------------
# Color palette — consistent modern theme
# ---------------------------------------------------------------------------
_PRIMARY = (15, 60, 120)       # Deep navy
_PRIMARY_LIGHT = (230, 240, 250)  # Very light blue
_ACCENT = (0, 105, 180)        # Bright blue for links/accents
_GREEN_BG = (22, 120, 75)      # Dark green (approval badge)
_GREEN_LIGHT = (232, 245, 233) # Light green tint
_AMBER_BG = (180, 120, 0)      # Dark amber (pend badge)
_AMBER_LIGHT = (255, 248, 225) # Light amber tint
_RED = (200, 40, 40)           # Red for critical/deadlines
_TEXT = (33, 37, 41)           # Near-black body text
_TEXT_LIGHT = (108, 117, 125)  # Muted secondary text
_TEXT_MUTED = (150, 150, 150)  # Footer/watermark text
_DIVIDER = (222, 226, 230)     # Light gray divider
_CARD_BG = (248, 249, 250)     # Card/alternating row background
_WHITE = (255, 255, 255)
_WARN_BG = (255, 248, 225)     # Warning banner
_WARN_TEXT = (133, 100, 4)     # Warning banner text


class _LetterPDF(FPDF):
    """Custom FPDF subclass for professional PA notification letters."""

    def __init__(self, letter_type: str, auth_number: str) -> None:
        super().__init__()
        self._letter_type = letter_type
        self._auth_number = auth_number

    def header(self) -> None:
        # Blue accent bar across the very top
        self.set_fill_color(*_PRIMARY)
        self.rect(0, 0, 210, 3, "F")

        self.set_y(8)

        # Organization name — left aligned
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*_PRIMARY)
        self.cell(0, 5, "UTILIZATION MANAGEMENT DEPARTMENT",
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        # Subtitle
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*_TEXT_LIGHT)
        self.cell(0, 4, "Prior Authorization Program",
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        # Thin divider
        self.ln(3)
        self.set_draw_color(*_DIVIDER)
        self.set_line_width(0.3)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(5)

    def footer(self) -> None:
        self.set_y(-18)
        # Thin divider above footer
        self.set_draw_color(*_DIVIDER)
        self.set_line_width(0.2)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(3)

        self.set_font("Helvetica", "I", 6.5)
        self.set_text_color(*_TEXT_MUTED)
        self.cell(0, 3.5,
                  "AI-Assisted Draft  --  Human Clinical Review Required Before Finalization",
                  align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.cell(
            0, 3.5,
            f"Ref: {self._auth_number}   |   Page {self.page_no()}/{{nb}}",
            align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT,
        )


def generate_letter_pdf(letter_dict: dict) -> str:
    """Generate a professional PDF notification letter.

    Returns base64-encoded PDF string.
    """
    letter_type = letter_dict.get("letter_type", "submission_ready")
    auth_number = letter_dict.get("authorization_number", "")
    patient_name = letter_dict.get("patient_name", "")
    provider_name = letter_dict.get("provider_name", "")
    effective_date = letter_dict.get("effective_date", "")
    expiration_date = letter_dict.get("expiration_date")
    appeal_rights = letter_dict.get("appeal_rights")
    doc_deadline = letter_dict.get("documentation_deadline")

    # Support both old ("approval") and new ("submission_ready") letter types
    is_ready = letter_type in ("submission_ready", "approval")

    pdf = _LetterPDF(letter_type=letter_type, auth_number=auth_number)
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=22)

    # ── Status badge ──────────────────────────────────────────
    if is_ready:
        badge_bg = _GREEN_BG
        badge_text = "READY TO SUBMIT"
        title_text = "Prior Authorization - Ready for Submission"
    else:
        badge_bg = _AMBER_BG
        badge_text = "DOCUMENTATION NEEDED"
        title_text = "Prior Authorization -- Documentation Action Required"

    # Title
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(*_PRIMARY)
    pdf.cell(0, 9, title_text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(3)

    # Status badge — centered colored pill
    badge_w = 70
    badge_x = (210 - badge_w) / 2
    badge_y = pdf.get_y()
    # Rounded rect background
    pdf.set_fill_color(*badge_bg)
    pdf.rect(badge_x, badge_y, badge_w, 9, "F")
    # Small rounded corners simulated with circles
    pdf.set_draw_color(*badge_bg)

    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*_WHITE)
    pdf.set_xy(badge_x, badge_y)
    pdf.cell(badge_w, 9, badge_text, align="C")
    pdf.set_xy(10, badge_y + 9)
    pdf.ln(6)

    # ── Disclaimer warning strip ───────────────────────────────────────
    pdf.set_fill_color(*_WARN_BG)
    pdf.set_font("Helvetica", "I", 6.5)
    pdf.set_text_color(*_WARN_TEXT)
    pdf.multi_cell(
        0, 3.5,
        "AI-ASSISTED DRAFT: All recommendations require human clinical review. "
        "Coverage policies reflect Medicare LCDs/NCDs only. "
        "Commercial and Medicare Advantage plans may differ.",
        fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT,
    )
    pdf.set_text_color(*_TEXT)
    pdf.ln(5)

    # ── Reference info row ─────────────────────────────────────────────
    ref_label = "Reference No."
    _info_row(pdf, [
        (ref_label, auth_number),
        ("Date", effective_date),
    ])
    pdf.ln(5)

    # ── Patient & Provider info cards (side by side) ───────────────────
    card_y = pdf.get_y()
    card_w = 90
    gap = 10

    # Patient card
    _info_card(pdf, 10, card_y, card_w, "Patient Information", [
        ("Name", patient_name),
        ("Date of Birth", letter_dict.get("patient_dob", "")),
        ("Insurance ID", letter_dict.get("insurance_id", "") or "Not provided"),
    ])

    # Provider card
    _info_card(pdf, 10 + card_w + gap, card_y, card_w, "Provider Information", [
        ("Name", provider_name),
        ("NPI", letter_dict.get("provider_npi", "")),
    ])

    # Move below the taller card
    pdf.set_y(card_y + 38)

    # ── Services table ─────────────────────────────────────────────────
    procedure_codes = letter_dict.get("procedure_codes", [])
    diagnosis_codes = letter_dict.get("diagnosis_codes", [])
    if procedure_codes or diagnosis_codes:
        heading = "Requested Services"
        _section_heading(pdf, heading)

        col_w = [35, 155]
        _table_header_row(pdf, [("Type", col_w[0]), ("Code(s)", col_w[1])])
        row_idx = 0
        if procedure_codes:
            _table_data_row(pdf, [
                ("Procedure (CPT)", col_w[0]),
                (", ".join(procedure_codes), col_w[1]),
            ], row_idx)
            row_idx += 1
        if diagnosis_codes:
            _table_data_row(pdf, [
                ("Diagnosis (ICD-10)", col_w[0]),
                (", ".join(diagnosis_codes), col_w[1]),
            ], row_idx)
        pdf.ln(5)

    # ── Coverage policy references ─────────────────────────────────────
    policy_refs = letter_dict.get("policy_references", [])
    if policy_refs:
        _section_heading(pdf, "Coverage Policy Reference")
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(*_TEXT)
        for ref in policy_refs:
            pdf.set_x(12)
            pdf.cell(4, 5, "-")
            pdf.multi_cell(0, 5, _safe(ref),
                           new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(3)

    # ── Reference validity (submission-ready) ──────────────────────────
    if is_ready and expiration_date:
        _section_heading(pdf, "Reference Validity")
        _info_row(pdf, [
            ("Prepared Date", effective_date),
            ("Reference Expiry", expiration_date),
        ])
        pdf.ln(5)

    # ── Clinical summary ───────────────────────────────────────────────
    summary = letter_dict.get("summary", "")
    if summary:
        _section_heading(pdf, "Clinical Summary")
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*_TEXT)
        pdf.multi_cell(0, 5, _safe(summary),
                       new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(4)

    # ── Clinical rationale ────────────────────────────────────────────
    rationale = letter_dict.get("clinical_rationale", "")
    if rationale:
        _section_heading(pdf, "Clinical Rationale")
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(*_TEXT)
        pdf.multi_cell(0, 4.5, _safe(rationale),
                       new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(4)

    # ── Clinician Override Notice ──────────────────────────────────────
    if letter_dict.get("was_overridden"):
        _section_heading(pdf, "Clinician Override Notice")
        _callout_box(
            pdf,
            "This decision was overridden by a clinician. "
            "The original AI recommendation was modified based on clinical judgment.",
            bg=_WARN_BG,
            text_color=_WARN_TEXT,
            bold=True,
            font_size=8,
        )
        pdf.ln(3)
        override_reviewer = letter_dict.get("override_reviewer", "")
        original_rec = letter_dict.get("original_recommendation", "")
        override_rationale = letter_dict.get("override_rationale", "")
        if override_reviewer:
            _info_row(pdf, [
                ("Override By", override_reviewer),
                ("Original AI Recommendation", original_rec.replace("_", " ").upper() if original_rec else "N/A"),
            ])
            pdf.ln(3)
        if override_rationale:
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(*_PRIMARY)
            pdf.cell(0, 6, "Override Rationale:",
                     new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(*_TEXT)
            pdf.multi_cell(0, 5, _safe(override_rationale),
                           new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(4)

    # ── Coverage criteria met ─────────────────────────────────────────
    criteria_met = letter_dict.get("coverage_criteria_met", [])
    criteria_not_met = letter_dict.get("coverage_criteria_not_met", [])
    if criteria_met or criteria_not_met:
        _section_heading(pdf, "Coverage Criteria Evaluation")
        if criteria_met:
            pdf.set_font("Helvetica", "B", 8.5)
            pdf.set_text_color(*_GREEN_BG)
            pdf.cell(0, 5, "Criteria Met:",
                     new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_text_color(*_TEXT)
            for item in criteria_met:
                pdf.set_x(12)
                pdf.set_font("Helvetica", "", 8.5)
                pdf.cell(4, 5, "-")
                pdf.multi_cell(0, 5, _safe(item),
                               new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        if criteria_not_met:
            pdf.ln(2)
            pdf.set_font("Helvetica", "B", 8.5)
            pdf.set_text_color(*_RED)
            pdf.cell(0, 5, "Criteria Not Met:",
                     new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_text_color(*_TEXT)
            for item in criteria_not_met:
                pdf.set_x(12)
                pdf.set_font("Helvetica", "", 8.5)
                pdf.cell(4, 5, "-")
                pdf.multi_cell(0, 5, _safe(item),
                               new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(4)

    # ── Documentation notes (for approval — non-critical gaps) ────────
    if is_ready:
        doc_gaps = letter_dict.get("documentation_gaps", [])
        if doc_gaps:
            _section_heading(pdf, "Documentation Notes")
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(*_TEXT_LIGHT)
            pdf.multi_cell(
                0, 4,
                "The following non-critical items were noted during review. "
                "These do not affect this authorization but may be useful "
                "for future submissions.",
                new_x=XPos.LMARGIN, new_y=YPos.NEXT,
            )
            pdf.set_text_color(*_TEXT)
            pdf.ln(2)
            for gap in doc_gaps:
                what = (gap.get("what", "") or gap.get("description", "")) if isinstance(gap, dict) else str(gap)
                pdf.set_x(12)
                pdf.set_font("Helvetica", "", 8.5)
                pdf.cell(4, 5, "-")
                pdf.multi_cell(0, 5, _safe(what),
                               new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(4)

    # ── Additional documentation required (pend) ───────────────────────
    if not is_ready:
        doc_gaps = letter_dict.get("documentation_gaps", [])
        if doc_gaps:
            _section_heading(pdf, "Additional Documentation Required")

            for gap in doc_gaps:
                what = (gap.get("what", "") or gap.get("description", "")) if isinstance(gap, dict) else str(gap)
                critical = gap.get("critical", False) if isinstance(gap, dict) else False
                tag = "REQUIRED" if critical else "Requested"
                tag_color = _RED if critical else _AMBER_BG

                pdf.set_x(12)
                pdf.set_font("Helvetica", "B", 7)
                pdf.set_text_color(*tag_color)
                pdf.cell(20, 5, f"[{tag}]")
                pdf.set_font("Helvetica", "", 8.5)
                pdf.set_text_color(*_TEXT)
                pdf.multi_cell(0, 5, _safe(what),
                               new_x=XPos.LMARGIN, new_y=YPos.NEXT)

                req = gap.get("request", "") if isinstance(gap, dict) else ""
                if req:
                    pdf.set_x(32)
                    pdf.set_font("Helvetica", "I", 8)
                    pdf.set_text_color(*_TEXT_LIGHT)
                    pdf.multi_cell(0, 4, _safe(req),
                                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                    pdf.set_text_color(*_TEXT)

            pdf.ln(4)

        # Deadline callout
        if doc_deadline:
            _callout_box(
                pdf,
                f"DEADLINE: Please submit the requested documentation by {doc_deadline}.",
                bg=_AMBER_LIGHT,
                text_color=_RED,
                bold=True,
            )
            pdf.ln(4)

    # ── Appeal rights (pend) ───────────────────────────────────────────
    if appeal_rights:
        _section_heading(pdf, "Appeal Rights")
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(*_TEXT)
        pdf.multi_cell(0, 4.5, _safe(appeal_rights),
                       new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(4)

    # ── Terms and conditions (approval) ────────────────────────────────
    if is_ready:
        _section_heading(pdf, "Terms and Conditions")
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*_TEXT_LIGHT)
        pdf.multi_cell(
            0, 4,
            "This authorization is valid for the services described above during "
            "the authorization period. Services must be rendered within the "
            "effective dates. This authorization does not guarantee payment. "
            "Payment is subject to eligibility verification at the time of service.",
            new_x=XPos.LMARGIN, new_y=YPos.NEXT,
        )
        pdf.ln(4)

    # ── Closing ────────────────────────────────────────────────────────
    pdf.ln(3)
    pdf.set_text_color(*_TEXT)
    pdf.set_font("Helvetica", "", 9.5)
    pdf.cell(0, 6, "Sincerely,", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(6)
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(0, 6, "Utilization Management Department",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # ── Bottom disclaimer bar ──────────────────────────────────────────
    pdf.ln(10)
    _callout_box(
        pdf,
        "DISCLAIMER: This is an AI-assisted draft. Coverage policies reflect "
        "Medicare LCDs/NCDs only. If this review is for a commercial or Medicare "
        "Advantage plan, payer-specific policies were not applied. All decisions "
        "require human clinical review before finalization.",
        bg=_WARN_BG,
        text_color=_WARN_TEXT,
        bold=False,
        font_size=6.5,
    )

    # ── Output to base64 ──────────────────────────────────────────────
    buf = io.BytesIO()
    pdf.output(buf)
    pdf_bytes = buf.getvalue()
    return base64.b64encode(pdf_bytes).decode("ascii")


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------

def _section_heading(pdf: FPDF, text: str) -> None:
    """Render a section heading with subtle left accent bar."""
    y = pdf.get_y()

    # Left accent bar
    pdf.set_fill_color(*_ACCENT)
    pdf.rect(10, y, 2, 7, "F")

    # Heading text
    pdf.set_x(15)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*_PRIMARY)
    pdf.cell(0, 7, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Light underline
    pdf.set_draw_color(*_DIVIDER)
    pdf.set_line_width(0.2)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(4)
    pdf.set_text_color(*_TEXT)


def _info_row(pdf: FPDF, pairs: list[tuple[str, str]]) -> None:
    """Render key-value pairs in a horizontal row with card background."""
    y = pdf.get_y()
    col_w = 190 / len(pairs)

    pdf.set_fill_color(*_CARD_BG)
    pdf.rect(10, y, 190, 14, "F")

    for i, (label, value) in enumerate(pairs):
        x = 10 + i * col_w + 4
        # Label
        pdf.set_xy(x, y + 1)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(*_TEXT_LIGHT)
        pdf.cell(col_w - 8, 4, label)
        # Value
        pdf.set_xy(x, y + 6)
        pdf.set_font("Helvetica", "B", 9.5)
        pdf.set_text_color(*_TEXT)
        pdf.cell(col_w - 8, 6, _safe(value))

    pdf.set_y(y + 14)


def _info_card(
    pdf: FPDF,
    x: float, y: float, w: float,
    title: str,
    rows: list[tuple[str, str]],
) -> None:
    """Render a bordered info card at a specific position."""
    row_h = 8
    title_h = 8
    h = title_h + len(rows) * row_h + 4

    # Card border
    pdf.set_draw_color(*_DIVIDER)
    pdf.set_line_width(0.3)
    pdf.rect(x, y, w, h)

    # Title bar
    pdf.set_fill_color(*_PRIMARY_LIGHT)
    pdf.rect(x, y, w, title_h, "F")

    pdf.set_xy(x + 4, y + 1)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*_PRIMARY)
    pdf.cell(w - 8, 6, title)

    # Data rows
    for i, (label, value) in enumerate(rows):
        ry = y + title_h + 2 + i * row_h
        pdf.set_xy(x + 4, ry)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(*_TEXT_LIGHT)
        pdf.cell(28, 4, f"{label}:")
        pdf.set_font("Helvetica", "B" if i == 0 else "", 8.5)
        pdf.set_text_color(*_TEXT)
        pdf.cell(w - 36, 4, _safe(value))


def _table_header_row(pdf: FPDF, columns: list[tuple[str, int]]) -> None:
    """Render a table header with primary background."""
    pdf.set_fill_color(*_PRIMARY_LIGHT)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*_PRIMARY)
    for label, width in columns:
        pdf.cell(width, 7, label, border=0, fill=True)
    pdf.ln()
    # Underline
    pdf.set_draw_color(*_ACCENT)
    pdf.set_line_width(0.4)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.set_text_color(*_TEXT)


def _table_data_row(
    pdf: FPDF,
    cells: list[tuple[str, int]],
    row_index: int,
) -> None:
    """Render a table data row with alternating background."""
    if row_index % 2 == 1:
        pdf.set_fill_color(*_CARD_BG)
    else:
        pdf.set_fill_color(*_WHITE)

    pdf.set_font("Helvetica", "", 8.5)
    for text, width in cells:
        pdf.cell(width, 6.5, _safe(text)[:80], border=0, fill=True)
    pdf.ln()


def _bullet_item(pdf: FPDF, text: str) -> None:
    """Render a simple bullet point."""
    pdf.set_x(12)
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(*_TEXT)
    pdf.cell(4, 5, "-")
    pdf.multi_cell(0, 5, _safe(text),
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _callout_box(
    pdf: FPDF,
    text: str,
    bg: tuple,
    text_color: tuple,
    bold: bool = False,
    font_size: float = 8,
) -> None:
    """Render a full-width callout/banner box."""
    pdf.set_fill_color(*bg)
    pdf.set_font("Helvetica", "BI" if bold else "I", font_size)
    pdf.set_text_color(*text_color)
    pdf.multi_cell(
        0, 4,
        _safe(text),
        fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT,
    )
    pdf.set_text_color(*_TEXT)


def _safe(value) -> str:
    """Convert value to a Latin-1-safe string for Helvetica rendering."""
    if value is None:
        return "N/A"
    s = str(value)
    s = s.replace("\u2022", "-")    # bullet
    s = s.replace("\u2014", "--")   # em dash
    s = s.replace("\u2013", "-")    # en dash
    s = s.replace("\u2018", "'")    # left single quote
    s = s.replace("\u2019", "'")    # right single quote
    s = s.replace("\u201c", '"')    # left double quote
    s = s.replace("\u201d", '"')    # right double quote
    s = s.replace("\u2026", "...")  # ellipsis
    s = s.encode("latin-1", errors="replace").decode("latin-1")
    return s
