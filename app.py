# =============================================================================
# PDF to Anki Cards Pro — Phase 6: The Universal Exporter (.apkg)
# =============================================================================
#
# WHAT'S NEW:
#   [1] Genanki Integration: Generates native Anki Packages (.apkg) via the 
#       new `generate_apkg` function.
#   [2] Dual Export UI: Auto-Send (AnkiConnect) or Download .apkg (Mobile).
#   [3] CSS Styling: The generated basic and cloze model templates feature 
#       clean HTML/CSS formatting for the Explanation field.
#
# HOW TO RUN:
#   streamlit run app.py
# =============================================================================


# ── Step 1: Streamlit FIRST ──────────────────────────────────────────────────
import streamlit as st

st.set_page_config(
    page_title="PDF to Anki Cards Pro",
    page_icon="🧠",
    layout="wide",
)

# ── Step 2: Safe Imports ─────────────────────────────────────────────────────
import tempfile
import os
import time
import traceback
import json
import re
import base64
import requests
import random
from pathlib import Path

GENAI_AVAILABLE = False
genai = None
types = None

try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError as e:
    st.error(
        f"❌ **Import Error:** Could not load `google-genai`.\n\n"
        f"**Error:** `{e}`\n\n"
        f"**Fix:** `pip install --upgrade google-genai`"
    )
except Exception as e:
    st.error(
        f"❌ **Unexpected Error** loading `google-genai`:\n\n"
        f"```\n{traceback.format_exc()}\n```\n\n"
        f"Try: `pip install --upgrade google-genai pydantic`"
    )

GENANKI_AVAILABLE = False
try:
    import genanki
    GENANKI_AVAILABLE = True
except ImportError as e:
    st.error(
        f"❌ **Import Error:** Could not load `genanki`.\n\n"
        f"**Error:** `{e}`\n\n"
        f"**Fix:** `pip install genanki`"
    )


# =============================================================================
# ── [PERSISTENT CONFIG] ─────────────────────────────────────────────────────
# =============================================================================

CONFIG_PATH = Path.home() / ".anki_pdf_config.json"


def load_config() -> dict:
    defaults = {
        "api_key_encoded": "",
        "total_cards_generated": 0,
        "preferred_model": "gemini-2.5-flash",
    }
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            defaults.update(saved)
    except Exception:
        pass
    return defaults


def save_config(config: dict):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    except Exception:
        pass


def encode_key(key: str) -> str:
    return base64.b64encode(key.encode("utf-8")).decode("utf-8")


def decode_key(encoded: str) -> str:
    try:
        return base64.b64decode(encoded.encode("utf-8")).decode("utf-8")
    except Exception:
        return ""


config = load_config()


# =============================================================================
# ── MODEL CONFIGURATION ─────────────────────────────────────────────────────
# =============================================================================

AVAILABLE_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
]


def get_fallback_list(primary: str) -> list:
    fallback = [primary]
    for m in AVAILABLE_MODELS:
        if m not in fallback:
            fallback.append(m)
    return fallback


# =============================================================================
# ── SUPPORTED FILE TYPES ────────────────────────────────────────────────────
# =============================================================================

MIME_TYPE_MAP = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
}

ACCEPTED_EXTENSIONS = list(MIME_TYPE_MAP.keys())
ACCEPTED_TYPES = [ext.lstrip(".") for ext in ACCEPTED_EXTENSIONS]


# =============================================================================
# ── HELPER FUNCTIONS ─────────────────────────────────────────────────────────
# =============================================================================

def call_gemini_with_fallback(client, contents, primary_model, json_mode=False):
    config_obj = None
    if json_mode:
        config_obj = types.GenerateContentConfig(
            response_mime_type="application/json",
        )

    fallback_list = get_fallback_list(primary_model)
    last_error = None

    for model_name in fallback_list:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=config_obj,
            )
            return response.text, model_name
        except Exception as e:
            error_str = str(e).lower()
            last_error = e
            if "404" in error_str or "not found" in error_str or "unavailable" in error_str:
                continue
            else:
                st.error(f"❌ Gemini API error: {e}")
                return None, None

    st.error(
        f"❌ All models failed. Last error: {last_error}\n\n"
        f"Models tried: {', '.join(fallback_list)}"
    )
    return None, None


@st.cache_resource
def get_gemini_client(api_key: str):
    return genai.Client(api_key=api_key)


def upload_file_to_gemini(client, file_bytes: bytes, filename: str):
    tmp_path = None
    try:
        _, ext = os.path.splitext(filename)
        ext = ext.lower() if ext else ".pdf"
        mime_type = MIME_TYPE_MAP.get(ext, "application/pdf")

        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        uploaded_file = client.files.upload(
            file=tmp_path,
            config=types.UploadFileConfig(
                display_name=filename,
                mime_type=mime_type,
            ),
        )

        while uploaded_file.state == "PROCESSING":
            time.sleep(1)
            uploaded_file = client.files.get(name=uploaded_file.name)

        if uploaded_file.state == "FAILED":
            st.error("❌ Gemini failed to process the file.")
            return None

        return uploaded_file

    except Exception as e:
        st.error(f"❌ Upload failed: {e}")
        return None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def get_document_preview(client, gemini_file, primary_model):
    contents = [
        gemini_file,
        "Analyze this document/image. Report in under 150 words: "
        "1) Topic, 2) Type (textbook/notes/paper/diagram/image), "
        "3) Key sections (max 5), 4) Visual elements found, "
        "5) Content density (fact-rich or conceptual).",
    ]
    text, _ = call_gemini_with_fallback(client, contents, primary_model)
    return text or "⚠️ Could not generate preview."


# =============================================================================
# ── ANKICONNECT INTEGRATION ──────────────────────────────────────────────────
# =============================================================================

def get_anki_decks() -> list:
    try:
        response = requests.post(
            "http://localhost:8765",
            json={"action": "deckNames", "version": 6},
            timeout=2
        )
        if response.status_code == 200:
            result = response.json()
            if not result.get("error"):
                decks = result.get("result", [])
                if decks:
                    return sorted(decks)
    except Exception:
        pass
    return ["Default"]


def send_to_anki(cards: list, target_deck: str):
    try:
        req_create = requests.post(
            "http://localhost:8765",
            json={
                "action": "createDeck",
                "version": 6,
                "params": {"deck": target_deck}
            },
            timeout=5
        )
        if req_create.status_code != 200 or req_create.json().get("error"):
            return False, req_create.json().get("error", "Error creating deck")

        notes = []
        for card in cards:
            tags = card.get("tags", ["medical"])
            explanation = card.get("explanation", "").strip()
            card_type = card.get("type", "basic")

            if card_type == "basic":
                back_content = card.get("back", "").replace("\n", "<br>")
                if explanation:
                    explanation_html = explanation.replace("\n", "<br>")
                    back_content += f"<br><br><b>Explanation:</b><br>{explanation_html}"
                
                note = {
                    "deckName": target_deck,
                    "modelName": "Basic",
                    "fields": {
                        "Front": card.get("front", "").replace("\n", "<br>"),
                        "Back": back_content
                    },
                    "options": {"allowDuplicate": False},
                    "tags": tags
                }
            elif card_type == "cloze":
                note = {
                    "deckName": target_deck,
                    "modelName": "Cloze",
                    "fields": {
                        "Text": card.get("text", "").replace("\n", "<br>"),
                        "Back Extra": explanation.replace("\n", "<br>")
                    },
                    "options": {"allowDuplicate": False},
                    "tags": tags
                }
            else:
                continue
                
            notes.append(note)

        req_add = requests.post(
            "http://localhost:8765",
            json={
                "action": "addNotes",
                "version": 6,
                "params": {"notes": notes}
            },
            timeout=10
        )
        result = req_add.json()
        
        if result.get("error"):
            return False, result["error"]
            
        added_count = sum(1 for note_id in result.get("result", []) if note_id is not None)
        return True, added_count

    except Exception as e:
        return False, str(e)


# =============================================================================
# ── [NEW] GENANKI UNIVERSAL EXPORTER (.apkg) ─────────────────────────────────
# =============================================================================

@st.cache_data(show_spinner=False)
def generate_apkg(cards: list, deck_name: str) -> bytes:
    """Generates an Anki .apkg binary file in-memory matching our format."""
    
    # Generate deterministic IDs based on the deck name
    # Using hash ensures consistency, keeping cards updated rather than duplicated
    deck_id = hash(deck_name) % (1<<31)
    if deck_id < 0: deck_id += (1<<31)
        
    my_deck = genanki.Deck(deck_id, deck_name)

    # Core CSS logic to replicate Anki visual formatting + our explanation block
    model_css = """
    .card {
      font-family: Arial, Helvetica, sans-serif;
      font-size: 20px;
      text-align: center;
      color: black;
      background-color: white;
      padding: 20px;
    }
    .explanation {
      margin-top: 25px;
      font-size: 16px;
      color: #004d40;
      text-align: left;
      border-top: 2px solid #e0e0e0;
      padding-top: 15px;
    }
    .cloze {
      font-weight: bold;
      color: #1a73e8;
    }
    """

    basic_model_id = (deck_id + 1) % (1<<31)
    basic_model = genanki.Model(
        basic_model_id,
        'Basic (PDF to Anki Pro)',
        fields=[
            {'name': 'Front'},
            {'name': 'Back'}
        ],
        templates=[
            {
                'name': 'Card 1',
                'qfmt': '{{Front}}',
                'afmt': '{{Front}}<hr id="answer">{{Back}}',
            },
        ],
        css=model_css
    )

    cloze_model_id = (deck_id + 2) % (1<<31)
    cloze_model = genanki.Model(
        cloze_model_id,
        'Cloze (PDF to Anki Pro)',
        model_type=genanki.Model.CLOZE,
        fields=[
            {'name': 'Text'},
            {'name': 'Back Extra'}
        ],
        templates=[
            {
                'name': 'Cloze',
                'qfmt': '{{cloze:Text}}',
                'afmt': '{{cloze:Text}}<br><br><div class="explanation">{{Back Extra}}</div>',
            },
        ],
        css=model_css
    )

    # ── Map JSON Cards to Genanki Notes ──
    for card in cards:
        tags = card.get("tags", ["medical"])
        explanation = card.get("explanation", "").strip()
        card_type = card.get("type", "basic")

        if card_type == "basic":
            back_content = card.get("back", "").replace("\n", "<br>")
            if explanation:
                exp_html = explanation.replace("\n", "<br>")
                back_content += f'<div class="explanation"><b>Explanation:</b><br>{exp_html}</div>'
            
            note = genanki.Note(
                model=basic_model,
                fields=[
                    card.get("front", "").replace("\n", "<br>"),
                    back_content
                ],
                tags=tags
            )
            my_deck.add_note(note)

        elif card_type == "cloze":
            b_extra = ""
            if explanation:
                b_extra = f'<b>Explanation:</b><br>{explanation.replace("\\n", "<br>")}'

            note = genanki.Note(
                model=cloze_model,
                fields=[
                    card.get("text", "").replace("\n", "<br>"),
                    b_extra
                ],
                tags=tags
            )
            my_deck.add_note(note)

    # Write to a fast temporary file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".apkg") as tmp:
        tmp_path = tmp.name
        
    genanki.Package(my_deck).write_to_file(tmp_path)
    
    # Read binary back
    with open(tmp_path, "rb") as f:
        package_bytes = f.read()
        
    os.unlink(tmp_path)
    return package_bytes


# =============================================================================
# ── DYNAMIC FLASHCARD PROMPT BUILDER ─────────────────────────────────────────
# =============================================================================

QUESTION_STYLES = [
    "Direct Recall (Standard)",
    "Clinical Vignette (USMLE Style)",
]


def build_flashcard_prompt(
    num_basic: int, num_cloze: int, tags: list[str],
    topic_focus: str, question_style: str, use_mnemonics: bool,
) -> str:
    total = num_basic + num_cloze
    tags_json = json.dumps(tags) if tags else '["medical"]'

    topic_instruction = ""
    if topic_focus.strip():
        topic_instruction = (
            f"\n**TOPIC FOCUS:** Focus EXCLUSIVELY on this specific topic: "
            f'"{topic_focus.strip()}". Ignore content unrelated to this topic.'
        )

    if question_style == "Clinical Vignette (USMLE Style)":
        style_instruction = (
            "Frame Basic card questions as brief clinical vignettes "
            "(USMLE Step 1/2 style): present a patient scenario with key "
            "findings, then ask for a diagnosis, mechanism, or next step."
        )
    else:
        style_instruction = (
            "Frame questions as direct recall — clear, concise questions "
            "testing one specific fact or concept."
        )

    mnemonic_instruction = ""
    if use_mnemonics:
        mnemonic_instruction = (
            "\nWhenever applicable, invent or provide a standard medical "
            "mnemonic to help memorize the concept, and include it in the "
            "`explanation` field."
        )

    prompt = f"""You are an expert medical educator and Anki flashcard creator.
Analyze the uploaded document/image thoroughly — ALL text, diagrams, images, tables, and visual elements.
{topic_instruction}

Generate EXACTLY {total} high-yield medical flashcards:
- **{num_basic} Basic cards** (front/back): Key definitions, anatomy, physiology, pathology, pharmacology.
- **{num_cloze} Cloze cards** (fill-in-the-blank): Clinical correlations, disease processes, mechanisms. Use {{{{c1::answer}}}} notation.

STYLE: {style_instruction}

CRITICAL RULES:
1. ALL content MUST be STRICTLY in English. Use precise medical terminology.
2. Each card tests ONE concept (atomic).
3. Answers must be concise but medically complete.
4. VISUAL PRIORITY: If you detect medical diagrams, charts, metabolic pathways, or anatomical images, you MUST create specific flashcards testing that visual information.
5. Every card MUST include the tags array: {tags_json}
6. Every card MUST include an "explanation" field with a concise rationale, clinical significance, or brief context for the answer.{mnemonic_instruction}

Return ONLY a JSON array with this structure:
[
  {{
    "type": "basic",
    "front": "Question here",
    "back": "Answer here",
    "explanation": "Brief rationale or clinical context here",
    "tags": {tags_json}
  }},
  {{
    "type": "cloze",
    "text": "The {{{{c1::answer}}}} is tested here.",
    "explanation": "Brief rationale or clinical context here",
    "tags": {tags_json}
  }}
]

Return ONLY valid JSON. No markdown, no extra text."""

    return prompt


def generate_flashcards(
    client, gemini_file, primary_model,
    num_basic, num_cloze, tags,
    topic_focus, question_style, use_mnemonics,
):
    prompt = build_flashcard_prompt(
        num_basic, num_cloze, tags,
        topic_focus, question_style, use_mnemonics,
    )
    contents = [gemini_file, prompt]

    raw_text, model_used = call_gemini_with_fallback(
        client, contents, primary_model, json_mode=True
    )

    if raw_text is None:
        return None, None

    try:
        cleaned = raw_text.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        cards = json.loads(cleaned)

        if not isinstance(cards, list):
            st.error("❌ Gemini returned invalid format (expected a list).")
            return None, None

        for card in cards:
            if "tags" not in card:
                card["tags"] = tags if tags else ["medical"]
            if "explanation" not in card:
                card["explanation"] = ""

        return cards, model_used

    except json.JSONDecodeError as e:
        st.error(f"❌ JSON parse error: {e}")
        with st.expander("🔍 Raw response (debugging)"):
            st.code(raw_text, language="json")
        return None, None


# =============================================================================
# ── MAIN APPLICATION ─────────────────────────────────────────────────────────
# =============================================================================

try:

    # ── Sidebar ──────────────────────────────────────────────────────────
    with st.sidebar:
        st.header("⚙️ App Settings")

        saved_key = decode_key(config.get("api_key_encoded", ""))
        api_key = st.text_input(
            "Google Gemini API Key",
            value=saved_key,
            type="password",
            placeholder="Paste your API key here...",
            help="Get your free key from https://aistudio.google.com/apikey",
        )

        if api_key:
            st.session_state["api_key"] = api_key
            if api_key != saved_key:
                config["api_key_encoded"] = encode_key(api_key)
                save_config(config)
            st.success("✅ API Key saved!")
        else:
            st.info("🔑 Please enter your API key to get started.")

        st.divider()

        st.subheader("🤖 Model Selection")
        saved_model = config.get("preferred_model", "gemini-2.5-flash")
        default_idx = 0
        if saved_model in AVAILABLE_MODELS:
            default_idx = AVAILABLE_MODELS.index(saved_model)

        selected_model = st.selectbox(
            "Gemini Model",
            options=AVAILABLE_MODELS,
            index=default_idx,
            help="Newer models may be unavailable; the app auto-falls back.",
        )

        if selected_model != saved_model:
            config["preferred_model"] = selected_model
            save_config(config)

        st.session_state["selected_model"] = selected_model
        st.markdown(f"**Active Model:** `{selected_model}`")

        st.divider()

        st.subheader("📊 Your Stats")
        lifetime_cards = config.get("total_cards_generated", 0)
        st.metric("🏆 Total Cards Generated", f"{lifetime_cards:,}")
        st.caption("Lifetime count across all sessions")

        st.divider()
        if GENAI_AVAILABLE:
            st.caption("✅ google-genai SDK loaded")
        else:
            st.caption("❌ google-genai SDK NOT loaded")

        if GENANKI_AVAILABLE:
            st.caption("✅ genanki Exporter loaded")
        else:
            st.caption("❌ genanki NOT loaded")

    # ── Main Title ───────────────────────────────────────────────────────
    st.title("🧠 PDF to Anki Cards Pro")
    st.markdown(
        "**Automatically generate high-yield Anki flashcards from your "
        "medical documents & images using Google Gemini AI.**"
    )
    st.divider()

    if not GENAI_AVAILABLE:
        st.error("⚠️ The `google-genai` SDK failed to load. Fix above.")
        st.stop()

    if not GENANKI_AVAILABLE:
        st.error("⚠️ The `genanki` library is missing. Install via `pip install genanki`.")
        st.stop()

    # ── Universal File Uploader ──────────────────────────────────────────
    uploaded_file = st.file_uploader(
        "📄 Step 1: Upload a Medical Document or Image",
        type=ACCEPTED_TYPES,
        help="Supports: PDF, PNG, JPG, DOCX, PPTX, CSV, XLSX, TXT (max 50 MB).",
    )

    if uploaded_file is not None:
        file_size_mb = uploaded_file.size / (1024 * 1024)

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric(label="📁 File Name", value=uploaded_file.name)
        with col2:
            st.metric(label="📏 File Size", value=f"{file_size_mb:.2f} MB")
        with col3:
            _, ext = os.path.splitext(uploaded_file.name)
            st.metric(label="📄 File Type", value=ext.upper() or "Unknown")

    # =============================================================
    # ── UNCONDITIONAL PRE-CONFIGURATION UI ───────────────────────
    # =============================================================
    
    with st.container(border=True):
        st.subheader("⚙️ Step 2: Configure Your Flashcards & Anki Target")

        # ── A. Flashcard Settings ──
        cfg_col1, cfg_col2 = st.columns(2)
        with cfg_col1:
            num_basic = st.slider("📗 Number of Basic Cards", min_value=0, max_value=50, value=10, step=1)
        with cfg_col2:
            num_cloze = st.slider("📙 Number of Cloze Cards", min_value=0, max_value=50, value=5, step=1)

        total_cards = num_basic + num_cloze
        
        tag_col, topic_col = st.columns(2)
        with tag_col:
            tags_input = st.text_input("🏷️ Tags (comma-separated)", value="medical")
            tags = [t.strip() for t in tags_input.split(",") if t.strip()]
            if not tags: tags = ["medical"]
        with topic_col:
            topic_focus = st.text_input("🎯 Specific Topic Focus (Optional)", placeholder="e.g., Only focus on Glycolysis")

        style_col, mnem_col = st.columns([2, 1])
        with style_col:
            question_style = st.selectbox("💬 Question Style", options=QUESTION_STYLES, index=0)
        with mnem_col:
            use_mnemonics = st.checkbox("🧩 Add Mnemonics", value=False)

        st.divider()

        # ── B. Target Settings ──
        col_d1, col_d2 = st.columns(2)
        live_decks = get_anki_decks()
        raw_options = ["[Create New Root Deck]"] + live_decks
        
        with col_d1:
            parent_deck = st.selectbox("🎯 Target Anki Deck (Parent)", options=raw_options)
        with col_d2:
            sub_deck = st.text_input("📁 Sub-deck Name (Optional)", placeholder="e.g., Bio Lecture 1")
            
        if parent_deck == "[Create New Root Deck]":
            target_deck = sub_deck.strip() if sub_deck.strip() else "New Medical Deck"
        else:
            target_deck = f"{parent_deck}::{sub_deck.strip()}" if sub_deck.strip() else parent_deck

        st.caption(f"📌 Cards will be mapped to exact Anki deck path: `{target_deck}`")
        
        st.divider()

        # ── C. Advanced Speed Option ──
        show_preview = st.toggle("🤖 Enable AI Document Summary Preview (Adds 10-15s to processing time)", value=False)


    # =============================================================
    # ── PHASE 6: THE GENERATION BUTTON ───────────────────────────
    # =============================================================

    st.write("") # Whitespace
    if total_cards == 0:
        st.warning("⚠️ Set at least 1 card to generate.")
        
    elif st.button(
        f"🚀 Generate {total_cards} Flashcards (Step 3)", 
        type="primary", 
        use_container_width=True,
        disabled=(uploaded_file is None)
    ):
        if "api_key" not in st.session_state or not st.session_state["api_key"]:
            st.warning("⚠️ Enter your Google Gemini API key in the sidebar first to proceed!")
            st.stop()
            
        file_key = f"{uploaded_file.name}_{uploaded_file.size}"
        already_uploaded = (
            "gemini_file" in st.session_state
            and "uploaded_file_key" in st.session_state
            and st.session_state["uploaded_file_key"] == file_key
        )
        
        primary_model = st.session_state.get("selected_model", "gemini-2.5-flash")
        client = get_gemini_client(st.session_state["api_key"])
        
        # Clear previous generation artifacts
        st.session_state.pop("flashcards", None)
        st.session_state.pop("anki_success", None)
        
        with st.status("🚀 Processing the Ultimate Medical Workflow...", expanded=True) as status:
            
            # Step 1: Uploading
            if not already_uploaded:
                st.write("☁️ Uploading & Processing file in Google Gemini...")
                file_bytes = uploaded_file.getvalue()
                gemini_file = upload_file_to_gemini(client, file_bytes, uploaded_file.name)
                
                if not gemini_file:
                    st.error("❌ File upload failed. Aborting.")
                    st.stop()
                    
                st.session_state["gemini_file"] = gemini_file
                st.session_state["uploaded_file_key"] = file_key
            else:
                gemini_file = st.session_state["gemini_file"]
                st.write("✅ File already uploaded (using cache).")

            # Step 2: Document Preview
            if show_preview:
                st.write("🤖 Analyzing document structure...")
                preview = get_document_preview(client, gemini_file, primary_model)
                st.session_state["document_preview"] = preview
            else:
                st.session_state["document_preview"] = None

            # Step 3: Flashcard Generation
            st.write(f"🧠 Prompting Gemini to generate {total_cards} highly targeted flashcards...")
            cards, model_used = generate_flashcards(
                client, gemini_file, primary_model,
                num_basic, num_cloze, tags,
                topic_focus, question_style, use_mnemonics,
            )
            
            if not cards:
                st.error("❌ Failed to parse AI flashcards. Aborting.")
                st.stop()
                
            st.session_state["flashcards"] = cards
            st.session_state["model_used"] = model_used
            st.session_state["last_target_deck"] = target_deck

            config["total_cards_generated"] = config.get("total_cards_generated", 0) + len(cards)
            save_config(config)

            status.update(label=f"✅ Step 3 Complete! Generated {len(cards)} cards.", state="complete", expanded=False)

        st.balloons()


    # =============================================================
    # ── PHASE 6: DUAL EXPORT UI (Step 4) ─────────────────────────
    # =============================================================
    
    if "flashcards" in st.session_state and st.session_state["flashcards"]:
        cards = st.session_state["flashcards"]
        target = st.session_state.get("last_target_deck", target_deck)

        st.divider()
        st.header("📥 Step 4: Export to Anki")
        
        # We present two columns for Export Options
        exp_col1, exp_col2 = st.columns(2)
        
        # ── OPTION A: AnkiConnect Auto-Send ──
        with exp_col1:
            st.info(
                "**Option A: Direct Auto-Send**\n\n"
                "Requires Anki Desktop to be OPEN right now with the AnkiConnect add-on installed."
            )
            if st.button("🚀 Auto-Send to Local Anki", use_container_width=True):
                with st.spinner(f"Sending {len(cards)} notes to `{target}` via AnkiConnect..."):
                    success, msg = send_to_anki(cards, target)
                if success:
                    st.success(f"🎉 Success! Sent {msg} cards to `{target}`.")
                else:
                    st.error(f"❌ Connection Failed:\n{msg}")

        # ── OPTION B: .apkg File Download ──
        with exp_col2:
            st.info(
                "**Option B: Universal Download**\n\n"
                "Generates an Anki Package file (`.apkg`). Works natively on Mac, PC, iOS, Android, and Cloud hosting!"
            )
            
            # Generate the binary file in-memory using caching for speed
            with st.spinner("Compiling .apkg package..."):
                apkg_bytes = generate_apkg(cards, target)
                
            # Clean deck name for the file name output
            clean_name = re.sub(r'[^A-Za-z0-9_\-\s]', '_', target).strip()
            if not clean_name: clean_name = "Medical_Deck"
            
            st.download_button(
                label=f"📱 Download Mobile Deck (.apkg)",
                data=apkg_bytes,
                file_name=f"{clean_name}.apkg",
                mime="application/octet-stream",
                use_container_width=True
            )

        # =============================================================
        # ── RESULTS DISPLAY SECTION ──────────────────────────────────
        # =============================================================
        st.divider()

        # Optional AI Preview
        if st.session_state.get("document_preview"):
            with st.expander("🤖 AI Document Analysis", expanded=False):
                st.markdown(st.session_state["document_preview"])

        # Card Preview Rendering
        model_used = st.session_state.get("model_used", "unknown")
        basic_cards = [c for c in cards if c.get("type") == "basic"]
        cloze_cards = [c for c in cards if c.get("type") == "cloze"]

        st.subheader("📋 Card Preview")
        
        s1, s2, s3 = st.columns(3)
        with s1: st.metric("📝 Total Cards Rendered", len(cards))
        with s2: st.metric("📗 Basic Cards", len(basic_cards))
        with s3: st.metric("📙 Cloze Cards", len(cloze_cards))
        st.caption(f"Generated optimally via `{model_used}`")

        tab_basic, tab_cloze, tab_json = st.tabs(["📗 Basic Cards", "📙 Cloze Cards", "📦 Raw JSON"])

        with tab_basic:
            if basic_cards:
                for i, card in enumerate(basic_cards, 1):
                    with st.container(border=True):
                        card_tags = card.get("tags", [])
                        tag_str = " · ".join(f"`{t}`" for t in card_tags) if card_tags else ""
                        st.markdown(f"**Card {i}**  {tag_str}")

                        front_col, back_col = st.columns(2)
                        with front_col:
                            st.markdown("**🔵 Front (Question):**")
                            st.info(card.get("front", "N/A"))
                        with back_col:
                            st.markdown("**🟢 Back (Answer):**")
                            st.success(card.get("back", "N/A"))

                        explanation = card.get("explanation", "")
                        if explanation:
                            st.markdown("**💡 Explanation:**")
                            st.caption(explanation)
            else:
                st.info("No Basic cards generated.")

        with tab_cloze:
            if cloze_cards:
                for i, card in enumerate(cloze_cards, 1):
                    with st.container(border=True):
                        card_tags = card.get("tags", [])
                        tag_str = " · ".join(f"`{t}`" for t in card_tags) if card_tags else ""
                        st.markdown(f"**Card {i}**  {tag_str}")

                        cloze_text = card.get("text", "N/A")
                        st.markdown("**📝 Cloze Text:**")
                        st.warning(cloze_text)

                        preview_text = re.sub(r"\{\{c\d+::(.*?)\}\}", r"**[\1]**", cloze_text)
                        st.markdown("**👁️ Preview:**")
                        st.markdown(preview_text)

                        explanation = card.get("explanation", "")
                        if explanation:
                            st.markdown("**💡 Explanation:**")
                            st.caption(explanation)
            else:
                st.info("No Cloze cards generated.")

        with tab_json:
            st.code(json.dumps(cards, indent=2, ensure_ascii=False), language="json")

except Exception as e:
    st.error(f"💥 **Application Error:** {e}")
    st.code(traceback.format_exc(), language="python")
    st.info(
        "💡 **Common fixes:**\n"
        "- `pip install --upgrade google-genai pydantic streamlit requests genanki`\n"
        "- Check your Python version (3.9+ required)"
    )
