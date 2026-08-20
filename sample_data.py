"""
Sample (fake) Valeo member data for the connector MVP.

IMPORTANT: This is the ONLY file that fakes anything. To go from demo to real,
replace each get_* function below with a call to the Valeo backend API using the
authenticated member's token. The MCP tool layer in server.py does not change.
"""

MEMBER = {
    "first_name": "Aisha",
    "city": "Dubai",
    "member_since": "2025-03-14",
    "plan": "Valeo Complete",
}

# --- Lab panel: 68 biomarkers across 9 categories ------------------------------
# Only aggregate counts + flags are exposed through the connector, never raw values.
LAB_PANEL = {
    "panel_name": "Valeo Complete Blood Panel",
    "collected_on": "2026-07-28",
    "collection_method": "At-home blood draw (Dubai Marina)",
    "categories": [
        {"category": "Heart",              "total": 9,  "in_range": 7, "out_of_range": 2,
         "flagged": [{"marker": "LDL Cholesterol", "direction": "high", "severity": "moderate"},
                     {"marker": "ApoB", "direction": "high", "severity": "mild"}]},
        {"category": "Metabolic",          "total": 8,  "in_range": 6, "out_of_range": 2,
         "flagged": [{"marker": "HbA1c", "direction": "high", "severity": "mild"},
                     {"marker": "Fasting Insulin", "direction": "high", "severity": "moderate"}]},
        {"category": "Vitamins & Minerals","total": 11, "in_range": 8, "out_of_range": 3,
         "flagged": [{"marker": "Vitamin D", "direction": "low", "severity": "significant"},
                     {"marker": "Ferritin", "direction": "low", "severity": "moderate"},
                     {"marker": "Vitamin B12", "direction": "low", "severity": "mild"}]},
        {"category": "Thyroid",            "total": 5,  "in_range": 5, "out_of_range": 0, "flagged": []},
        {"category": "Liver",              "total": 7,  "in_range": 7, "out_of_range": 0, "flagged": []},
        {"category": "Kidney",             "total": 6,  "in_range": 6, "out_of_range": 0, "flagged": []},
        {"category": "Blood & Immunity",   "total": 12, "in_range": 11, "out_of_range": 1,
         "flagged": [{"marker": "hs-CRP", "direction": "high", "severity": "mild"}]},
        {"category": "Hormones",           "total": 7,  "in_range": 6, "out_of_range": 1,
         "flagged": [{"marker": "Cortisol (AM)", "direction": "high", "severity": "mild"}]},
        {"category": "Female Health",      "total": 3,  "in_range": 3, "out_of_range": 0, "flagged": []},
    ],
    "trends": {
        "Vitamin D": "improving (was significantly low in Feb 2026)",
        "HbA1c": "improving (down from 5.9% equivalent band in Feb 2026)",
        "LDL Cholesterol": "stable",
        "Ferritin": "worsening slightly since Feb 2026",
    },
}

# --- Programs -----------------------------------------------------------------
PROGRAMS = [
    {
        "name": "Metabolic Reset (12 weeks)",
        "status": "active",
        "week": 9,
        "total_weeks": 12,
        "started_on": "2026-06-15",
        "coach": "Dr. Layla H., Metabolic Health",
        "adherence_pct": 84,
        "goals": ["Reduce visceral fat", "Improve insulin sensitivity", "Build strength 3x/week"],
        "metrics": [
            {"metric": "Weight",       "start": "78.4 kg", "current": "72.9 kg", "target": "70.0 kg", "direction": "on track"},
            {"metric": "Waist",        "start": "92 cm",   "current": "85 cm",   "target": "82 cm",   "direction": "on track"},
            {"metric": "Body fat",     "start": "34%",     "current": "29%",     "target": "27%",     "direction": "on track"},
            {"metric": "Steps/day",    "start": "4,100",   "current": "8,600",   "target": "8,000",   "direction": "goal met"},
        ],
        "coach_notes": "Strong progress on movement and sleep. Protein intake still short on weekends. Next milestone review 2026-08-31.",
    },
    {
        "name": "Iron & Vitamin D Correction",
        "status": "active",
        "week": 4,
        "total_weeks": 8,
        "started_on": "2026-07-29",
        "coach": "Nour A., Clinical Nutrition",
        "adherence_pct": 91,
        "goals": ["Restore ferritin above 50 ng/mL", "Restore vitamin D to optimal range"],
        "metrics": [
            {"metric": "Supplement adherence", "start": "-", "current": "91%", "target": ">85%", "direction": "goal met"},
        ],
        "coach_notes": "Re-test ferritin and vitamin D at week 8. Take iron with vitamin C, away from coffee and dairy.",
    },
]

# --- Appointments -------------------------------------------------------------
APPOINTMENTS = [
    {"date": "2026-08-24", "time": "07:30", "service": "At-home blood draw (follow-up panel)",
     "clinician": "Valeo phlebotomy team", "location": "Home - Dubai Marina", "status": "confirmed",
     "prep": "12-hour fast, water only"},
    {"date": "2026-08-27", "time": "18:00", "service": "IV Drip - Iron + B-complex",
     "clinician": "Nurse Mariam K.", "location": "Home - Dubai Marina", "status": "confirmed",
     "prep": "Eat a light meal beforehand, allow 45 minutes"},
    {"date": "2026-09-02", "time": "11:00", "service": "Coach check-in - Metabolic Reset week 12 review",
     "clinician": "Dr. Layla H.", "location": "Video call", "status": "confirmed", "prep": "None"},
]

RECENT_SERVICES = [
    {"date": "2026-07-28", "service": "At-home blood draw - Valeo Complete Panel", "outcome": "Results delivered 2026-07-31"},
    {"date": "2026-07-14", "service": "IV Drip - Hydration + Vitamin C", "outcome": "Completed"},
    {"date": "2026-06-30", "service": "Physiotherapy - lower back (session 3 of 4)", "outcome": "Completed"},
]

# --- Supplements --------------------------------------------------------------
SUPPLEMENT_PLAN = {
    "prescribed_by": "Nour A., Clinical Nutrition",
    "last_updated": "2026-07-31",
    "next_review": "2026-09-23",
    "items": [
        {"name": "Vitamin D3 + K2", "dose": "4,000 IU", "timing": "Morning, with fats",
         "reason": "Vitamin D significantly below optimal range", "linked_marker": "Vitamin D"},
        {"name": "Iron bisglycinate", "dose": "25 mg", "timing": "Every other day, with vitamin C",
         "reason": "Low ferritin with mild B12 insufficiency", "linked_marker": "Ferritin"},
        {"name": "Methylcobalamin (B12)", "dose": "1,000 mcg", "timing": "Morning, sublingual",
         "reason": "B12 in low-normal band", "linked_marker": "Vitamin B12"},
        {"name": "Omega-3 (EPA/DHA)", "dose": "2 g", "timing": "With dinner",
         "reason": "Elevated ApoB and hs-CRP", "linked_marker": "ApoB"},
        {"name": "Magnesium glycinate", "dose": "300 mg", "timing": "Before bed",
         "reason": "Sleep quality support alongside elevated AM cortisol", "linked_marker": "Cortisol (AM)"},
    ],
    "notes": "Do not take iron and magnesium in the same sitting. Pause iron 48h before the follow-up blood draw.",
}
