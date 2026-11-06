"""SeniorSahay shared engine.

Plain Python rules decide eligibility. No AI model ever decides eligibility or invents scheme facts.
An optional language model (only if ANTHROPIC_API_KEY is set) turns free text into structured fields;
its output is whitelisted and checked before use.
"""
import collections
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "_data", "schemes.json")
MAX_ASK = 5
_DB = None


def load_db():
    global _DB
    if _DB is None:
        with open(DB_PATH, encoding="utf-8") as f:
            _DB = json.load(f)
    return _DB


# ------------------------------------------------------------------ HTTP helpers
_hits = collections.defaultdict(collections.deque)


def rate_limited(ip, bucket, limit, window=60):
    """Best-effort per-instance limiter (serverless instances do not share memory)."""
    now = time.time()
    q = _hits[(ip, bucket)]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


class JsonHandler(BaseHTTPRequestHandler):
    bucket = "api"
    limit = 30
    max_body = 20000

    def log_message(self, *args):  # never log request data
        pass

    def client_ip(self):
        xff = self.headers.get("x-forwarded-for", "")
        return xff.split(",")[0].strip() or self.client_address[0]

    def send_bytes(self, code, body, ctype="application/json; charset=utf-8", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, code, obj):
        self.send_bytes(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > self.max_body:
            raise ValueError("size")
        data = json.loads(self.rfile.read(n))
        if not isinstance(data, dict):
            raise ValueError("shape")
        return data

    def guard(self):
        if rate_limited(self.client_ip(), self.bucket, self.limit):
            self.send_json(429, {"error": 429})
            return False
        return True


# ------------------------------------------------------------------ bilingual text
# index 0 = English, 1 = Telugu
TXT = {
    "age_min": ("Age {v} or above", "వయస్సు {v} లేదా అంతకంటే ఎక్కువ"),
    "age_max": ("Age {v} or below", "వయస్సు {v} లేదా అంతకంటే తక్కువ"),
    "ap": ("Lives in Andhra Pradesh", "ఆంధ్రప్రదేశ్‌లో నివసిస్తున్నారు"),
    "white_card": ("Has a white ration card", "తెల్ల రేషన్ కార్డు ఉంది"),
    "low_income": ("Belongs to a low-income (BPL) family", "తక్కువ ఆదాయం (బీపీఎల్) కుటుంబానికి చెందినవారు"),
    "disability_cert": ("Has an official disability certificate", "అధికారిక వైకల్య ధృవీకరణ పత్రం ఉంది"),
    "farmer": ("Is a farmer", "రైతు"),
    "land_owner": ("Family owns farm land", "కుటుంబానికి వ్యవసాయ భూమి ఉంది"),
    "student": ("Is a student", "విద్యార్థి"),
    "female": ("Is a woman", "మహిళ"),
    "rural": ("Lives in a village (rural area)", "గ్రామీణ ప్రాంతంలో నివసిస్తున్నారు"),
    "urban": ("Lives in a town or city", "పట్టణం లేదా నగరంలో నివసిస్తున్నారు"),
    "no_house": ("Does not own a pucca (permanent) house", "సొంత పక్కా ఇల్లు లేదు"),
    "cat_sc": ("Belongs to a Scheduled Caste (SC)", "షెడ్యూల్డ్ కులానికి (SC) చెందినవారు"),
    "cat_sc_st": ("Belongs to SC or ST", "SC లేదా ST వర్గానికి చెందినవారు"),
}

# rule name -> (profile field, test)
RULES = {
    "age_min": ("age", lambda v, a: v >= a),
    "age_max": ("age", lambda v, a: v <= a),
    "ap": ("ap", lambda v, a: v is True),
    "white_card": ("white_card", lambda v, a: v is True),
    "low_income": ("low_income", lambda v, a: v is True),
    "disability_cert": ("disability_cert", lambda v, a: v is True),
    "farmer": ("farmer", lambda v, a: v is True),
    "land_owner": ("land_owner", lambda v, a: v is True),
    "student": ("student", lambda v, a: v is True),
    "female": ("female", lambda v, a: v is True),
    "rural": ("rural", lambda v, a: v is True),
    "urban": ("rural", lambda v, a: v is False),
    "no_house": ("own_house", lambda v, a: v is False),
    "cat_sc": ("category", lambda v, a: v == "sc"),
    "cat_sc_st": ("category", lambda v, a: v in ("sc", "st")),
}

DOCS = {
    "aadhaar": ("Aadhaar card", "ఆధార్ కార్డు"),
    "ration": ("Ration card", "రేషన్ కార్డు"),
    "white_ration": ("White ration card", "తెల్ల రేషన్ కార్డు"),
    "age_proof": ("Age proof (Aadhaar or birth certificate)", "వయస్సు రుజువు (ఆధార్ లేదా జనన ధృవీకరణ)"),
    "bank": ("Bank passbook (account linked to Aadhaar)", "బ్యాంకు పాస్‌బుక్ (ఆధార్‌తో అనుసంధానమైన ఖాతా)"),
    "photo": ("Passport-size photo", "పాస్‌పోర్ట్ సైజు ఫోటో"),
    "mobile": ("Mobile number", "మొబైల్ నంబరు"),
    "land": ("Land ownership records (pattadar passbook)", "భూమి యాజమాన్య పత్రాలు (పట్టాదారు పాస్‌బుక్)"),
    "crop_loss": ("Crop details and a photo or report of the damage", "పంట వివరాలు, నష్టం ఫోటో లేదా నివేదిక"),
    "income": ("Income certificate", "ఆదాయ ధృవీకరణ పత్రం"),
    "caste": ("Caste certificate", "కుల ధృవీకరణ పత్రం"),
    "disability": ("Disability certificate (SADAREM or UDID)", "వైకల్య ధృవీకరణ పత్రం (సదరం లేదా UDID)"),
    "marks": ("Previous marks memo and certificates", "మునుపటి మార్కుల మెమో, సర్టిఫికెట్లు"),
    "admission": ("College admission proof and fee receipt", "కాలేజీ అడ్మిషన్ రుజువు, ఫీజు రసీదు"),
    "project": ("Short project report for the business", "వ్యాపారానికి చిన్న ప్రాజెక్టు నివేదిక"),
    "residence": ("Residence proof", "నివాస ధృవీకరణ"),
    "edu": ("Education certificates", "విద్యా ధృవపత్రాలు"),
    "site": ("House site or land document", "ఇంటి స్థలం లేదా భూమి పత్రం"),
}

CHANNELS = {
    "secretariat": ("Ask at your village or ward secretariat.", "మీ గ్రామ లేదా వార్డు సచివాలయంలో అడగండి."),
    "portal": ("Apply on the official website shown below.", "కింద చూపిన అధికారిక వెబ్‌సైట్‌లో దరఖాస్తు చేయండి."),
    "csc": ("A Common Service Centre (CSC) can help you apply.", "కామన్ సర్వీస్ సెంటర్ (CSC) దరఖాస్తుకు సహాయం చేస్తుంది."),
    "bank": ("Visit your bank branch.", "మీ బ్యాంకు శాఖను సంప్రదించండి."),
    "agri": ("Contact your Rythu Seva Kendram or agriculture officer.", "మీ రైతు సేవా కేంద్రం లేదా వ్యవసాయ అధికారిని సంప్రదించండి."),
    "college": ("Ask your college scholarship or fee office.", "మీ కాలేజీ స్కాలర్‌షిప్ లేదా ఫీజు విభాగాన్ని అడగండి."),
    "gp": ("Apply at your Gram Panchayat.", "మీ గ్రామ పంచాయతీలో దరఖాస్తు చేయండి."),
    "hospital": ("Ask at the help desk of an empanelled hospital (Arogya Mitra).", "గుర్తింపు పొందిన ఆసుపత్రిలోని ఆరోగ్య మిత్రను కలవండి."),
    "local": ("Ask at your district or municipal office.", "మీ జిల్లా లేదా మున్సిపల్ కార్యాలయంలో అడగండి."),
}

TOPIC_LABEL = {
    "senior": ("older people", "వృద్ధులు"), "pension": ("pension", "పింఛన్"),
    "health": ("health care", "ఆరోగ్య సేవలు"), "disability": ("disability support", "దివ్యాంగుల సహాయం"),
    "farming": ("farming", "వ్యవసాయం"), "insurance": ("insurance", "బీమా"),
    "education": ("education", "చదువు"), "education_sc": ("education", "చదువు"),
    "scholarship": ("scholarships", "స్కాలర్‌షిప్"), "loan": ("loans", "రుణాలు"),
    "business": ("starting a business", "వ్యాపారం ప్రారంభించడం"), "employment": ("jobs and work", "ఉద్యోగం, పని"),
    "skill": ("skill training", "నైపుణ్య శిక్షణ"), "housing": ("housing", "ఇల్లు"),
    "food": ("food support", "ఆహార సహాయం"), "financial": ("financial help", "ఆర్థిక సహాయం"),
    "welfare": ("support for low-income families", "తక్కువ ఆదాయ కుటుంబాలకు సహాయం"),
}

STATUS_LABEL = {
    "potentially_relevant": ("May apply to you (verified information)", "మీకు వర్తించవచ్చు (ధృవీకరించిన సమాచారం)"),
    "needs_verification": ("May apply to you – details still being verified", "మీకు వర్తించవచ్చు – వివరాలు ఇంకా ధృవీకరించాలి"),
    "more_info_needed": ("More information needed", "మరింత సమాచారం కావాలి"),
    "not_met": ("Does not appear to meet the listed conditions", "ఇచ్చిన షరతులకు సరిపోయినట్లు లేదు"),
}

VERIFY_LABEL = {
    "verified": ("Verified from a government source", "ప్రభుత్వ మూలం నుండి ధృవీకరించబడింది"),
    "partial": ("Partly verified – confirm the rest on the official site", "పాక్షికంగా ధృవీకరించబడింది – మిగిలినవి అధికారిక సైట్‌లో నిర్ధారించుకోండి"),
    "unverified": ("Not yet verified – please confirm on the official site", "ఇంకా ధృవీకరించలేదు – అధికారిక సైట్‌లో నిర్ధారించుకోండి"),
}

# follow-up questions: field -> (type, English, Telugu, options)
YN = "yn"
QUESTIONS = {
    "need": ("text",
             "Please tell me your situation in your own words. For example: your age, where you live, and what help you need.",
             "మీ పరిస్థితిని మీ మాటల్లో చెప్పండి. ఉదాహరణకు: మీ వయస్సు, మీరు ఎక్కడ ఉంటారు, ఏ సహాయం కావాలి.", None),
    "age": ("num", "How old are you?", "మీ వయస్సు ఎంత?", None),
    "ap": (YN, "Do you live in Andhra Pradesh?", "మీరు ఆంధ్రప్రదేశ్‌లో నివసిస్తున్నారా?", None),
    "white_card": (YN, "Do you have a white ration card?", "మీ దగ్గర తెల్ల రేషన్ కార్డు ఉందా?", None),
    "low_income": (YN, "Is your family income low (for example a BPL or white ration card family)?",
                   "మీ కుటుంబ ఆదాయం తక్కువగా ఉందా? (ఉదా: బీపీఎల్ లేదా తెల్ల రేషన్ కార్డు కుటుంబం)", None),
    "disability_cert": (YN, "Do you have an official disability certificate (such as SADAREM or UDID)? An illness like paralysis alone is not a certificate.",
                        "మీ దగ్గర అధికారిక వైకల్య ధృవీకరణ పత్రం (సదరం లేదా UDID) ఉందా? పక్షవాతం వంటి అనారోగ్యం మాత్రమే సర్టిఫికెట్ కాదు.", None),
    "farmer": (YN, "Are you a farmer?", "మీరు రైతా?", None),
    "land_owner": (YN, "Does your family own farm land (in your name or a family member's name)?",
                   "మీ కుటుంబానికి సొంత వ్యవసాయ భూమి ఉందా?", None),
    "student": (YN, "Are you studying in a college or university now?", "మీరు ప్రస్తుతం కాలేజీ లేదా విశ్వవిద్యాలయంలో చదువుతున్నారా?", None),
    "female": ("choice", "Are you a woman or a man?", "మీరు మహిళా లేదా పురుషులా?",
               [("woman", "Woman", "మహిళ"), ("man", "Man", "పురుషుడు")]),
    "rural": (YN, "Do you live in a village (rural area)?", "మీరు గ్రామీణ ప్రాంతంలో నివసిస్తున్నారా?", None),
    "own_house": (YN, "Do you already own a pucca (permanent, concrete) house?", "మీకు ఇప్పటికే సొంత పక్కా ఇల్లు ఉందా?", None),
    "category": ("choice", "Which social category do you belong to? You may skip this.",
                 "మీరు ఏ సామాజిక వర్గానికి చెందినవారు? ఇది చెప్పకపోయినా పర్వాలేదు.",
                 [("sc", "SC", "SC"), ("st", "ST", "ST"), ("obc", "OBC", "OBC"), ("general", "Other", "ఇతర")]),
}
FIELD_PRIORITY = ["age", "ap", "farmer", "student", "female", "disability_cert", "land_owner",
                  "rural", "low_income", "white_card", "own_house", "category"]
NEEDS = {"pension", "health", "housing", "loan", "business", "employment", "skill", "education",
         "scholarship", "insurance", "financial", "food"}
BOOL_FIELDS = ["white_card", "low_income", "disability_cert", "disability_mention", "farmer", "land_owner",
               "student", "unemployed", "female", "rural", "own_house", "crop_damage"]


# ------------------------------------------------------------------ language understanding (rules)
_TE = str.maketrans("౦౧౨౩౪౫౬౭౮౯", "0123456789")
_HI = str.maketrans("०१२३४५६७८९", "0123456789")


def norm(text):
    return str(text).lower().replace("\u2019", "'").translate(_TE).translate(_HI)


def has(t, words):
    for w in words:
        if w.isascii():
            if re.search(r"\b" + re.escape(w), t):
                return True
        elif w in t:
            return True
    return False


KW = {
    "farmer": ["farm", "agricultur", "crop", "paddy", "cultivat", "rythu", "రైతు", "వ్యవసాయ", "పంట", "సేద్య"],
    "student": ["student", "studying", "b.tech", "btech", "m.tech", "engineering", "degree", "diploma",
                "intermediate", "university", "విద్యార్థి", "చదువుతున్న", "కాలేజీ"],
    "unemployed": ["unemployed", "jobless", "no job", "without a job", "నిరుద్యోగ", "ఉద్యోగం లేదు", "పని లేదు"],
    "disability": ["disab", "handicap", "divyang", "wheelchair", "cannot walk", "can't walk", "blind", "deaf",
                   "paralys", "stroke", "వికలాంగ", "దివ్యాంగ", "వైకల్య", "నడవలేను", "నడవ లేను", "పక్షవాత"],
    "cert": ["disability certificate", "sadarem", "udid", "సదరం", "వైకల్య ధృవీకరణ"],
    "low_income": ["low income", "low-income", "poor", "bpl", "below poverty", "no income", "can't afford",
                   "cannot afford", "పేద", "తక్కువ ఆదాయం", "ఆదాయం లేదు", "బీపీఎల్"],
    "white_card": ["white ration", "white card", "rice card", "తెల్ల రేషన్", "తెల్ల కార్డు", "బియ్యం కార్డు"],
    "female": ["woman", "women", "female", "lady", "widow", "మహిళ", "స్త్రీ"],
    "rural": ["village", "rural", "gram ", "పల్లె", "గ్రామం", "గ్రామీణ"],
    "urban": ["city", "town", "urban", "municipal", "పట్టణం", "నగరం"],
    "no_house": ["no house", "don't have a house", "do not have a house", "homeless", "hut", "kutcha", "kachha",
                 "ఇల్లు లేదు", "ఇల్లు లేక"],
    "land": ["my land", "own land", "acre", "hectare", "భూమి ఉంది", "ఎకరా", "ఎకరం", "పొలం ఉంది"],
    "landless": ["no land", "landless", "భూమి లేదు", "పొలం లేదు"],
    "crop_damage": ["damag", "flood", "drought", "pest", "crop loss", "destroyed", "rain", "వర్షం", "వరద",
                    "కరువు", "దెబ్బతిన్న", "నష్టం"],
}
NEED_KW = {
    "pension": ["pension", "పింఛన్", "పెన్షన్", "పింఛను"],
    "health": ["health", "hospital", "treatment", "medical", "surgery", "operation", "medicine", "illness", "sick",
               "cancer", "dialysis", "paralys", "stroke", "ఆరోగ్య", "ఆసుపత్రి", "వైద్య", "చికిత్స", "పక్షవాత", "అనారోగ్య"],
    "housing": ["house", "housing", "roof", "shelter", "homeless", "ఇల్లు", "ఇంటి", "గృహ"],
    "loan": ["loan", "credit", "రుణ", "అప్పు"],
    "business": ["business", "self-employ", "self employ", "startup", "start-up", "enterprise", "shop", "వ్యాపార",
                 "స్వయం ఉపాధి", "దుకాణ"],
    "employment": ["job", "employment", "jobless", "unemployed", "ఉద్యోగ", "ఉపాధి", "నిరుద్యోగ"],
    "skill": ["skill", "training", "vocational", "నైపుణ్య", "శిక్షణ"],
    "education": ["scholarship", "fees", "tuition", "education", "college", "study", "studying", "student",
                  "చదువ", "విద్యా", "స్కాలర్‌షిప్", "ఫీజు"],
    "scholarship": ["scholarship", "స్కాలర్‌షిప్"],
    "insurance": ["insurance", "బీమా"],
    "financial": ["financial", "money", "cash", "income support", "ఆర్థిక", "డబ్బు"],
    "food": ["food", "grain", "hungry", "బియ్యం", "ఆహార"],
}
AP_WORDS = ["andhra", "ఆంధ్ర", "guntur", "గుంటూరు", "vijayawada", "విజయవాడ", "visakhapatnam", "vizag", "విశాఖ",
            "tirupati", "తిరుపతి", "kurnool", "కర్నూలు", "nellore", "నెల్లూరు", "kakinada", "rajahmundry",
            "anantapur", "ananthapur", "కడప", "kadapa", "ongole", "ఒంగోలు"]
OTHER_STATES = ["telangana", "tamil nadu", "karnataka", "kerala", "maharashtra", "odisha", "orissa", "bihar",
                "uttar pradesh", "gujarat", "rajasthan", "madhya pradesh", "west bengal", "punjab", "haryana",
                "delhi", "assam", "jharkhand", "chhattisgarh", "తెలంగాణ", "తమిళనాడు", "కర్ణాటక", "కేరళ",
                "మహారాష్ట్ర", "ఒడిశా", "hyderabad", "హైదరాబాద్", "chennai", "bengaluru", "bangalore", "mumbai"]
AGE_PATTERNS = [
    r"(\d{1,3})\s*[- ]?\s*(?:years?|yrs?)[- ]*old",
    r"\bage[d]?\s*(?:is|of|:)?\s*(\d{1,3})\b",
    r"\bi\s*(?:am|'m)\s*(\d{1,3})\b(?!\s*(?:lakh|rupee|acre))",
    r"(\d{1,3})\s*(?:ఏళ్ళ|ఏళ్ల|ఏళ్లు|ఏళ్ళు|ఏండ్ల|సంవత్సరాల)",
    r"(?:వయస్సు|వయసు)\s*(?:ఎంత)?\s*(\d{1,3})",
]
YES_W = ["yes", "yeah", "yep", "ya", "ok", "okay", "sure", "correct", "right", "have", "అవును", "ఉంది",
         "ఉన్నాయి", "ఉన్నది", "ఔను", "हाँ", "हां", "जी"]
NO_W = ["no", "nope", "not", "don't", "dont", "do not", "none", "లేదు", "లేవు", "కాదు", "नहीं", "नही"]
DK_W = ["don't know", "dont know", "not sure", "unsure", "maybe", "తెలియదు", "చెప్పలేను"]
MALE_W = ["man", "male", "boy", "gentleman", "పురుష", "మగ"]
CAT_W = [("sc", [r"\bsc\b", "scheduled caste", "ఎస్సీ"]), ("st", [r"\bst\b", "scheduled tribe", "ఎస్టీ"]),
         ("obc", [r"\bobc\b", "backward", "బీసీ", "ఓబీసీ"]),
         ("general", ["general", r"\boc\b", "other", "open", "none", "ఇతర", "జనరల్"])]


def extract(text):
    """Rule-based extraction of facts the person stated. Sets only what is clearly said."""
    t = norm(text)
    u, needs = {}, set()
    for pat in AGE_PATTERNS:
        m = re.search(pat, t)
        if m and 1 <= int(m.group(1)) <= 120:
            u["age"] = int(m.group(1))
            break
    if has(t, AP_WORDS) or re.search(r"\bap\b", t):
        u["state"] = "AP"
    elif has(t, OTHER_STATES):
        u["state"] = "OTHER"
    if has(t, KW["farmer"]):
        u["farmer"] = True
    if has(t, KW["student"]):
        u["student"] = True
    if has(t, KW["unemployed"]):
        u["unemployed"] = True
    if has(t, KW["disability"]):
        u["disability_mention"] = True
        needs.add("health")
    if has(t, KW["cert"]) and not re.search(r"(no|not|don't|dont|without|లేదు)\W+(?:\w+\W+){0,3}(certificate|sadarem|udid)", t):
        u["disability_cert"] = True
    if has(t, KW["low_income"]) or re.search(
            r"income\s*(?:is|are|was)?\s*(?:very\s*)?(?:low|less)|low\s+family\s+income|(?:poor|weak)\s+family", t):
        u["low_income"] = True
    if has(t, KW["white_card"]):
        u["white_card"] = True
    if has(t, KW["female"]):
        u["female"] = True
    elif re.search(r"\bi\s*(?:am|'m)\s*a\s*(?:man|male)\b|పురుషుడు", t):
        u["female"] = False
    if has(t, KW["rural"]):
        u["rural"] = True
    elif has(t, KW["urban"]):
        u["rural"] = False
    if has(t, KW["no_house"]):
        u["own_house"] = False
    if has(t, KW["landless"]):
        u["land_owner"] = False
    elif has(t, KW["land"]):
        u["land_owner"] = True
        u["farmer"] = True
    if (u.get("farmer") or "crop" in t or "పంట" in t) and has(t, KW["crop_damage"]):
        u["crop_damage"] = True
        needs |= {"insurance", "financial"}
    for need, words in NEED_KW.items():
        if has(t, words):
            needs.add(need)
    if re.search(r"\bsc\b|scheduled caste|ఎస్సీ", t):
        u["category"] = "sc"
    elif re.search(r"\bst\b|scheduled tribe|ఎస్టీ", t):
        u["category"] = "st"
    elif re.search(r"\bobc\b|ఓబీసీ", t):
        u["category"] = "obc"
    if needs:
        u["needs"] = sorted(needs)
    return u


def clean_updates(u):
    """Whitelist and type-check any dict that came from outside (client profile or language model)."""
    out = {}
    if not isinstance(u, dict):
        return out
    a = u.get("age")
    if isinstance(a, (int, float)) and not isinstance(a, bool) and 1 <= int(a) <= 120:
        out["age"] = int(a)
    if u.get("state") in ("AP", "OTHER"):
        out["state"] = u["state"]
    if u.get("category") in ("sc", "st", "obc", "general"):
        out["category"] = u["category"]
    for k in BOOL_FIELDS:
        if isinstance(u.get(k), bool):
            out[k] = u[k]
    if isinstance(u.get("needs"), list):
        out["needs"] = sorted({n for n in u["needs"] if n in NEEDS})
    for k in ("skipped", "asked", "retried"):
        if isinstance(u.get(k), list):
            out[k] = [x for x in u[k] if isinstance(x, str) and x in QUESTIONS][:20]
    return out


def merge(p, u, overwrite=True):
    for k, v in u.items():
        if k == "needs":
            p["needs"] = sorted(set(p.get("needs", [])) | set(v))
        elif overwrite or p.get(k) is None:
            p[k] = v


def llm_extract(text):
    """Optional: structured extraction with a language model. Returns None when not configured or on any failure."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import requests
        prompt = (
            "Extract only facts the person clearly stated about themselves (message may be in any Indian language). "
            "Return ONLY a JSON object with optional keys: age (integer), state ('AP' if Andhra Pradesh else 'OTHER'), "
            "farmer, student, unemployed, female, rural, disability_mention, disability_cert, low_income, white_card, "
            "land_owner, crop_damage (booleans), category ('sc','st','obc','general'), "
            "needs (list from: pension, health, housing, loan, business, employment, skill, education, scholarship, "
            "insurance, financial, food). Do not guess; omit unknown keys. Message: " + json.dumps(text))
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5"), "max_tokens": 300,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=12)
        if r.status_code != 200:
            return None
        raw = r.json()["content"][0]["text"]
        m = re.search(r"\{.*\}", raw, re.S)
        return clean_updates(json.loads(m.group(0))) if m else None
    except Exception:
        return None


def parse_yn(text):
    t = norm(text)
    if has(t, DK_W):
        return "dk"
    if has(t, NO_W):
        return False
    if has(t, YES_W):
        return True
    return None


def apply_answer(p, field, text):
    """Apply the person's reply to the question that was asked. Returns True if understood."""
    t = norm(text)
    q = QUESTIONS.get(field)
    if not q:
        return False
    if has(t, DK_W):
        p.setdefault("skipped", []).append(field)
        return True
    if field == "need":
        u = extract(text)
        merge(p, u)
        return bool(u)
    if field == "age":
        m = re.search(r"\d{1,3}", t)
        if m and 1 <= int(m.group()) <= 120:
            p["age"] = int(m.group())
            return True
        return False
    if field == "category":
        for k, pats in CAT_W:
            if any(re.search(x, t) for x in pats):
                p["category"] = k
                return True
        return False
    if field == "female":
        if has(t, KW["female"]) or has(t, ["girl"]):
            p["female"] = True
            return True
        if has(t, MALE_W):
            p["female"] = False
            return True
        return False
    v = parse_yn(t)
    if v is None:
        return False
    if field == "ap":
        p["state"] = "AP" if v else "OTHER"
    else:
        p[field] = v
    return True


# ------------------------------------------------------------------ eligibility engine
def val(p, f):
    if f == "ap":
        s = p.get("state")
        return None if s is None else s == "AP"
    if f == "low_income":
        v = p.get("low_income")
        if v is None and p.get("white_card") is True:
            return True
        return v
    return p.get(f)


def eval_rule(rule, p):
    if isinstance(rule, dict):  # any-of
        subs = [eval_rule(r, p) for r in rule["any"]]
        met = [s for s in subs if s["state"] == "met"]
        if met:
            return met[0]
        ids = [i for s in subs for i in s["ids"]]
        miss = [s for s in subs if s["state"] == "missing"]
        if miss:
            return {"state": "missing", "fields": [f for s in miss for f in s["fields"]], "ids": ids}
        return {"state": "unmet", "fields": [], "ids": ids}
    name = rule[0]
    arg = rule[1] if len(rule) > 1 else None
    field, test = RULES[name]
    v = val(p, field)
    if v is None:
        return {"state": "missing", "fields": [field], "ids": [(name, arg)]}
    return {"state": "met" if test(v, arg) else "unmet", "fields": [field], "ids": [(name, arg)]}


def user_topics(p):
    t = set(p.get("needs", []))
    if p.get("farmer"):
        t.add("farming")
    if p.get("student"):
        t.add("education")
        if p.get("category") == "sc":
            t.add("education_sc")
    if p.get("unemployed"):
        t |= {"employment", "skill"}
    if p.get("disability_mention") or p.get("disability_cert"):
        t.add("disability")
    needs = set(p.get("needs", []))
    if p.get("age") and p["age"] >= 60 and (not needs or needs & {"pension", "health", "financial"}):
        t.add("senior")
    if (p.get("low_income") or p.get("white_card")) and not needs:
        t.add("welfare")
    if p.get("student") and p.get("category") == "sc":
        t.add("education_sc")
    return t


def evaluate(p, db=None):
    db = db or load_db()
    topics = user_topics(p)
    items = []
    for s in db["schemes"]:
        strong = topics - {"financial"}
        overlap = (strong if strong else topics) & set(s["topics"])
        if not overlap:
            continue
        evals = [eval_rule(r, p) for r in s["rules"]]
        if any(e["state"] == "unmet" for e in evals):
            status = "not_met"
        elif any(e["state"] == "missing" for e in evals):
            status = "more_info_needed"
        else:
            status = "potentially_relevant" if s["verification"] == "verified" else "needs_verification"
        boost = 2 if ("insurance" in topics and p.get("crop_damage") and "insurance" in s["topics"]) else 0
        items.append({"scheme": s, "status": status, "evals": evals, "overlap": sorted(overlap),
                      "score": len(overlap) + boost + (1 if s["level"] == "state" else 0),
                      "missing_fields": [f for e in evals if e["state"] == "missing" for f in e["fields"]]})
    order = {"potentially_relevant": 0, "needs_verification": 1, "more_info_needed": 2, "not_met": 3}
    items.sort(key=lambda i: (order[i["status"]], -i["score"], i["scheme"]["id"]))
    return items


def pick_followup(p, items):
    asked = p.get("asked", [])
    if len(asked) >= MAX_ASK:
        return None
    count = collections.Counter()
    for it in items:
        if it["status"] == "more_info_needed":
            count.update(it["missing_fields"])
    blocked = set(asked) | set(p.get("skipped", []))
    fieldmap = {"ap": "ap", "own_house": "own_house"}
    cands = [f for f in FIELD_PRIORITY if count[fieldmap.get(f, f)] and f not in blocked]
    if not cands:
        return None
    return max(cands, key=lambda f: (count[f], -FIELD_PRIORITY.index(f)))


# ------------------------------------------------------------------ rendering
def rule_text(ids, L):
    return " / ".join(TXT[n][L].format(v=a) for n, a in ids)


def host(url):
    return re.sub(r"^https?://(www\.)?", "", url).rstrip("/")


def render_item(it, lang):
    s = it["scheme"]
    L = 0 if lang == "en" else 1
    conds = [{"text": rule_text(e["ids"], L), "state": e["state"]} for e in it["evals"]]
    why = [("It is about " if L == 0 else "ఇది దీనికి సంబంధించినది: ") +
           ", ".join(dict.fromkeys(TOPIC_LABEL[x][L] for x in it["overlap"]))]
    why += [c["text"] for c in conds if c["state"] == "met"]
    docs = [DOCS[d][L] for d in s["docs"]]
    apply = [CHANNELS[c][L] for c in s["apply"]]
    if L == 0 and s.get("apply_en"):
        apply.append(s["apply_en"])
    name = s["name_en"] if L == 0 else s["name_te"]
    purpose = s["purpose_en"] if L == 0 else s["purpose_te"]
    benefit = s["benefit_en"] if L == 0 else s["benefit_te"]
    if L == 0:
        speak = (f"{name}. {purpose} {benefit} Documents you need: {'; '.join(docs)}. "
                 f"How to apply: {' '.join(apply)} Official website: {host(s['link'])}. "
                 "This is for guidance only. Please confirm on the official website.")
    else:
        speak = (f"{name}. {purpose} {benefit} కావలసిన పత్రాలు: {'; '.join(docs)}. "
                 f"ఎలా దరఖాస్తు చేయాలి: {' '.join(apply)} అధికారిక వెబ్‌సైట్: {host(s['link'])}. "
                 "ఇది మార్గదర్శకానికి మాత్రమే. దయచేసి అధికారిక వెబ్‌సైట్‌లో నిర్ధారించుకోండి.")
    return {
        "id": s["id"], "name": name, "dept": s["dept"], "level": s["level"], "category": s["category"],
        "status": it["status"], "status_label": STATUS_LABEL[it["status"]][L],
        "why": why, "conditions": conds, "purpose": purpose, "benefit": benefit,
        "docs": docs, "apply": apply, "link": s["link"], "source": s["source"],
        "verification": s["verification"], "verification_label": VERIFY_LABEL[s["verification"]][L],
        "last_verified": s.get("last_verified"), "note": s.get("notes") if L == 0 else None,
        "speak": speak[:1500],
    }


def run_turn(profile_in, text, asking, skip, lang):
    """One conversation turn. The client keeps the profile; nothing is stored on the server."""
    lang = lang if lang in ("te", "en") else "te"
    L = 0 if lang == "en" else 1
    p = clean_updates(profile_in)
    p.setdefault("needs", [])
    p.setdefault("asked", [])
    p.setdefault("skipped", [])
    p.setdefault("retried", [])
    llm_used = False
    retry = False
    if asking not in QUESTIONS:
        asking = None
    if skip and asking:
        p["skipped"].append(asking)
    elif text and asking:
        ok = apply_answer(p, asking, text)
        merge(p, extract(text), overwrite=False)
        if not ok and asking not in p["retried"]:
            p["retried"].append(asking)
            retry = True
    elif text:
        merge(p, extract(text))
        llm = llm_extract(text)
        if llm:
            merge(p, llm, overwrite=False)
            llm_used = True

    items = evaluate(p)
    topics = user_topics(p)
    field = None
    if retry:
        field = asking
    elif not topics:
        field = "need" if p["asked"].count("need") < 2 else None
    else:
        field = pick_followup(p, items)
    followup = None
    if field:
        if not retry and field != "need":
            p["asked"].append(field)
        elif field == "need" and not retry:
            p["asked"].append("need")
        typ, qen, qte, opts = QUESTIONS[field]
        question = (qen if L == 0 else qte)
        if retry:
            question = ("Sorry, I did not understand. " if L == 0 else "క్షమించండి, అర్థం కాలేదు. ") + question
        if typ == YN:
            options = [{"value": "yes", "label": "Yes" if L == 0 else "అవును"},
                       {"value": "no", "label": "No" if L == 0 else "లేదు"}]
        elif typ == "choice":
            options = [{"value": v, "label": (e if L == 0 else te)} for v, e, te in opts]
        else:
            options = []
        followup = {"field": field, "type": typ, "question": question, "options": options}

    shown = [i for i in items if i["status"] != "not_met"]
    results = [render_item(i, lang) for i in items]
    n_main = len([i for i in items if i["status"] in ("potentially_relevant", "needs_verification")])
    if followup:
        say = followup["question"]
    elif n_main:
        names = "; ".join(r["name"] for r in results if r["status"] in ("potentially_relevant", "needs_verification"))
        say = (f"I found {n_main} scheme(s) that may help you: {names}. Please listen to the details below."
               if L == 0 else f"మీకు ఉపయోగపడే {n_main} పథకాలు కనిపించాయి: {names}. వివరాలు కింద వినండి.")
    elif shown:
        say = ("I need a little more information to check these schemes." if L == 0
               else "ఈ పథకాలను తనిఖీ చేయడానికి మరికొంత సమాచారం కావాలి.")
    else:
        say = ("I could not find a matching scheme in my database for these details. Please ask at your village or ward "
               "secretariat, or check myscheme.gov.in." if L == 0 else
               "ఈ వివరాలకు సరిపోయే పథకం నా డేటాబేస్‌లో కనబడలేదు. దయచేసి మీ గ్రామ లేదా వార్డు సచివాలయంలో అడగండి, లేదా myscheme.gov.in చూడండి.")
    return {"profile": p, "followup": followup, "results": results, "say": say,
            "no_match": not shown, "llm_used": llm_used, "topics": sorted(topics)}
