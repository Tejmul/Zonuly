"""Market segments — what a company actually does, placed on an industry chain.

The atlas needs nodes a person would recognise and click: "AI infrastructure",
"Fintech", "Dev tools" — not "Pay not stated yet: 1,550". So every company is filed
under one *segment*, and segments sit in *layers* that run down the chain from the
ground up (infrastructure → AI models & tooling → developer tools → applications →
services), the way the chokepoints atlas ran from raw materials to applications.

Classification is rules over the company's own words — its description, and the
industries YC lists for it — first match wins, most specific first. A vertical
("AI for hospitals") beats the generic word in it ("AI"), because the vertical is what
you would say in conversation. A company can *also* match other segments; those
secondary matches become the links on the map. No model call, no guess: a company
with no description is "Unclassified", shown as such.

Layering: pure functions over strings. Imported by network.py; imports nothing above db.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

LAYERS: list[dict] = [
    {"key": "infra",  "code": "L1", "label": "Infrastructure",     "blurb": "Cloud, data, security, hardware — what everything else runs on."},
    {"key": "ai",     "code": "L2", "label": "AI models & tooling", "blurb": "Models, inference, ML platforms, agents — the layer we build in."},
    {"key": "dev",    "code": "L3", "label": "Developer tools",    "blurb": "Tools, APIs and platforms sold to engineers."},
    {"key": "apps",   "code": "L4", "label": "Applications",       "blurb": "Software for an industry: finance, health, sales, logistics…"},
    {"key": "other",  "code": "L5", "label": "Services & other",   "blurb": "Agencies, consulting, and companies we could not place."},
]


@dataclass(frozen=True)
class Segment:
    id: str
    layer: str
    label: str
    pattern: re.Pattern[str]
    blurb: str


def _rx(*alts: str) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(alts) + r")\b", re.I)


# Order = priority. Verticals first (the specific claim), then the AI layer, then dev
# tools, then infrastructure, then the broad application words, then services.
SEGMENTS: list[Segment] = [
    # ---- L4 applications: specific verticals
    Segment("apps:fintech", "apps", "Fintech & payments", _rx(
        r"fintech", r"payments?", r"banking", r"neobank", r"lending", r"loans?", r"credit", r"insur(?:ance|tech)",
        r"accounting", r"bookkeeping", r"tax", r"treasury", r"crypto", r"defi", r"blockchain", r"web3", r"wealth",
        r"trading", r"brokerage", r"invoic(?:e|ing)", r"expense", r"billing", r"payroll", r"remittance", r"fraud"),
        "Money moves through it: payments, banking, lending, insurance, crypto."),
    Segment("apps:health", "apps", "Healthcare & bio", _rx(
        r"health(?:care|tech)?", r"medical", r"clinic(?:al|s)?", r"biotech", r"bio(?:logy|informatics)?", r"pharma",
        r"patients?", r"hospitals?", r"therap(?:y|ist|eutics)", r"drug", r"diagnos(?:is|tic)", r"dental", r"mental health",
        r"telehealth", r"life sciences?", r"genomics?"),
        "Patients, clinics, drugs, biology."),
    Segment("apps:legal", "apps", "Legal & compliance", _rx(
        r"legal(?:tech)?", r"law(?: firms?)?", r"lawyers?", r"contracts?", r"regulat(?:ory|ion)", r"compliance",
        r"audit(?:ing)?", r"governance", r"privacy", r"policy"),
        "Contracts, regulation, audit, privacy."),
    Segment("apps:robotics", "apps", "Robotics & autonomy", _rx(
        r"robot(?:ics|s)?", r"autonomous", r"self-driving", r"drones?", r"humanoid", r"embodied"),
        "Machines that move in the world."),
    Segment("apps:climate", "apps", "Climate & energy", _rx(
        r"climate", r"energy", r"solar", r"batter(?:y|ies)", r"carbon", r"sustainab(?:le|ility)", r"grid", r"ev",
        r"electric vehicles?", r"nuclear", r"renewables?", r"cleantech"),
        "Energy, carbon, the grid."),
    Segment("apps:logistics", "apps", "Logistics, supply chain & industrial", _rx(
        r"logistics", r"supply chains?", r"freight", r"shipping", r"trucking", r"fleet", r"procurement",
        r"manufacturing", r"industrial", r"factor(?:y|ies)", r"warehous(?:e|ing)", r"delivery", r"maritime", r"aviation"),
        "Moving and making physical things."),
    Segment("apps:realestate", "apps", "Real estate & construction", _rx(
        r"real estate", r"proptech", r"construction", r"housing", r"property management", r"properties for",
        r"rentals?", r"mortgages?", r"landlords?", r"tenants?", r"homebuyers?", r"contractors?"),
        "Property, construction, housing."),
    Segment("apps:education", "apps", "Education", _rx(
        r"education", r"edtech", r"learning", r"students?", r"schools?", r"tutor(?:ing)?", r"courses?", r"universit(?:y|ies)"),
        "Teaching and learning."),
    Segment("apps:hr", "apps", "HR & recruiting", _rx(
        r"recruit(?:ing|ment|ers?)", r"hiring platform", r"hr", r"human resources", r"talent", r"workforce",
        r"staffing", r"employees?", r"onboarding", r"interviews?"),
        "Finding, paying and managing people."),
    Segment("apps:sales", "apps", "Sales, marketing & customer", _rx(
        r"sales", r"marketing", r"crm", r"customer (?:support|service|success|experience)", r"go-to-market", r"gtm",
        r"outbound", r"advertis(?:ing|ers?)", r"adtech", r"growth", r"leads?", r"revenue", r"call cent(?:er|re)",
        r"contact cent(?:er|re)", r"support (?:agents?|tickets?)"),
        "Selling to, and serving, customers."),
    Segment("apps:commerce", "apps", "Commerce & retail", _rx(
        r"e-?commerce", r"retail(?:ers?)?", r"shopify", r"shopping", r"merchants?", r"restaurants?", r"food",
        r"grocery", r"consumer goods", r"brands?", r"d2c", r"dtc"),
        "Buying and selling to consumers."),
    Segment("apps:gov", "apps", "Government, defense & space", _rx(
        r"defen[cs]e", r"government", r"govtech", r"public sector", r"space", r"aerospace", r"satellites?", r"military"),
        "The state, the sky."),
    Segment("apps:agri", "apps", "Agriculture & food tech", _rx(
        r"agricultur(?:e|al)", r"agtech", r"farm(?:s|ers|ing)?", r"crops?", r"livestock", r"food (?:tech|production|supply)",
        r"fisher(?:y|ies)", r"soil"),
        "Growing things."),
    Segment("apps:automotive", "apps", "Automotive & mobility", _rx(
        r"automotive", r"dealerships?", r"cars?", r"vehicles?", r"mobility", r"ride[- ]?(?:hailing|sharing)", r"transit",
        r"micromobility", r"parking", r"trucks?"),
        "Vehicles and getting around."),
    Segment("apps:deeptech", "apps", "Deep tech, materials & science", _rx(
        r"materials?", r"chemistry", r"chemicals?", r"physics", r"power electronics", r"batteries? (?:chemistry|materials)",
        r"engineering firms?", r"mining", r"minerals?", r"metals?", r"lab(?:oratory)?", r"scientific", r"r&d",
        r"simulation", r"cad", r"physical (?:engineering|world|systems)", r"hard ?tech", r"deep ?tech", r"components?"),
        "Atoms, not bits."),
    Segment("apps:media", "apps", "Media, gaming & creative", _rx(
        r"gaming", r"games?", r"media", r"video", r"music", r"creators?", r"entertainment", r"3d", r"animation",
        r"design tools?", r"content creation", r"streaming", r"podcasts?", r"film"),
        "Things people watch, play and make."),

    # ---- L2 AI models & tooling
    Segment("ai:models", "ai", "Foundation models & labs", _rx(
        r"foundation models?", r"frontier", r"research lab", r"train(?:ing|s)? (?:large |foundation )?models?",
        r"(?:large )?language models? (?:from scratch|research)", r"agi", r"superintelligence", r"pretraining"),
        "Building the models themselves."),
    Segment("ai:inference", "ai", "LLM inference & AI infrastructure", _rx(
        r"inference", r"model (?:serving|deployment|hosting)", r"llm (?:infrastructure|infra|gateway|ops)", r"ai infrastructure",
        r"fine-?tun(?:e|ing)", r"gpu (?:cloud|clusters?)", r"model routing", r"ai gateway", r"embeddings?", r"vector (?:database|db|search)"),
        "Running models fast and cheap."),
    Segment("ai:mlops", "ai", "ML platforms, data & evals", _rx(
        r"mlops", r"ml platform", r"feature stores?", r"experiment tracking", r"model monitoring", r"data labeling",
        r"annotation", r"synthetic data", r"evaluations?", r"evals", r"ai observability", r"llm observability",
        r"training data", r"rlhf", r"red[- ]teaming", r"ai safety", r"guardrails"),
        "Data, evaluation and the plumbing around models."),
    Segment("ai:agents", "ai", "AI agents & automation", _rx(
        r"ai agents?", r"agents?", r"agentic", r"workflow automation", r"copilots?", r"autonomous (?:agents?|workflows?)",
        r"ai (?:assistants?|employees?|teammates?)", r"automat(?:e|es|ing|ion)"),
        "Software that does the task, not just answers."),
    Segment("ai:perception", "ai", "Voice, vision & speech", _rx(
        r"voice", r"speech", r"computer vision", r"vision", r"image generation", r"video generation", r"multimodal",
        r"text-to-speech", r"speech-to-text", r"transcription", r"avatars?"),
        "Models that hear, see and speak."),

    # ---- L3 developer tools
    Segment("dev:code-ai", "dev", "AI for coding", _rx(
        r"code generation", r"coding (?:assistant|agent|copilot)", r"ai (?:for|pair) (?:developers|programmers|coding)",
        r"code review", r"software engineering agents?", r"writes? code", r"codebase"),
        "Tools that write and review code."),
    Segment("dev:tools", "dev", "Dev tools, APIs & platforms", _rx(
        r"developer tools?", r"devtools?", r"apis?", r"sdks?", r"open[- ]source", r"cli", r"ide", r"testing", r"ci/cd",
        r"deploy(?:ment|s)?", r"backend as a service", r"low-?code", r"no-?code", r"integration platform", r"webhooks?",
        r"documentation", r"package", r"frameworks?", r"for developers", r"developer platform", r"headless"),
        "Sold to engineers, used in the terminal."),
    Segment("dev:comms", "dev", "Communication & messaging APIs", _rx(
        r"messaging", r"email (?:api|infrastructure|delivery)", r"notifications?", r"sms", r"communication(?:s)? (?:api|platform)",
        r"chat api", r"video api", r"calling"),
        "Email, SMS, chat and video as a service."),

    # ---- L1 infrastructure
    Segment("infra:security", "infra", "Security & identity", _rx(
        r"security", r"cybersecurity", r"identity", r"authentication", r"authorization", r"iam", r"zero trust",
        r"appsec", r"pentest(?:ing)?", r"vulnerabilit(?:y|ies)", r"threat", r"secrets? management", r"encryption"),
        "Keeping systems and identities safe."),
    Segment("infra:data", "infra", "Data infrastructure", _rx(
        r"databases?", r"data (?:infrastructure|platform|pipelines?|engineering|stack|lake|lakehouse)", r"warehouse",
        r"etl", r"elt", r"streaming", r"analytics", r"business intelligence", r"bi", r"dashboards?", r"sql", r"postgres",
        r"data quality", r"data catalog", r"reverse etl"),
        "Where data is stored, moved and queried."),
    Segment("infra:observability", "infra", "Observability & reliability", _rx(
        r"observability", r"monitoring", r"logging", r"tracing", r"incident(?:s)?", r"on-?call", r"reliability", r"sre",
        r"uptime", r"performance monitoring", r"error tracking"),
        "Knowing when production is on fire."),
    Segment("infra:cloud", "infra", "Cloud, compute & networking", _rx(
        r"cloud", r"compute", r"gpus?", r"kubernetes", r"k8s", r"serverless", r"hosting", r"bare[- ]metal", r"data ?cent(?:er|re)s?",
        r"edge computing", r"cdn", r"networking", r"virtual machines?", r"containers?", r"infrastructure as code",
        r"platform engineering", r"finops", r"cloud costs?"),
        "Machines, networks and the bill for them."),
    Segment("infra:hardware", "infra", "Hardware, chips & IoT", _rx(
        r"semiconductors?", r"chips?", r"silicon", r"hardware", r"sensors?", r"iot", r"devices?", r"wearables?",
        r"embedded", r"firmware", r"photonics", r"quantum"),
        "Physical computing."),

    # ---- L4 applications: broad
    Segment("apps:productivity", "apps", "Productivity & collaboration", _rx(
        r"productivity", r"collaboration", r"notes?", r"docs", r"documents?", r"project management", r"knowledge (?:management|base)",
        r"spreadsheets?", r"meetings?", r"calendar", r"email client", r"workspace", r"search (?:for|across) (?:your|company)",
        r"enterprise search", r"internal tools?", r"back[- ]office", r"operations"),
        "Work tools for everyone."),
    Segment("apps:consumer", "apps", "Consumer & social", _rx(
        r"consumer", r"social", r"dating", r"communit(?:y|ies)", r"fitness", r"travel", r"wellness", r"hobby",
        r"marketplace", r"mobile app", r"lifestyle", r"pets?", r"parenting", r"personal finance"),
        "Apps people use for themselves."),
    Segment("apps:software", "apps", "Other B2B software", _rx(
        r"b2b", r"saas", r"enterprise", r"platform", r"software", r"vertical", r"smbs?", r"small businesses"),
        "Business software we could not place more precisely."),

    # ---- L5 services & other
    Segment("other:services", "other", "Agencies, consulting & services", _rx(
        r"agency", r"agencies", r"consult(?:ing|ancy)", r"services", r"studio", r"outsourc(?:e|ing)", r"staff augmentation",
        r"dev shop", r"freelanc(?:e|ers?)"),
        "People selling hours."),
    Segment("other:community", "other", "Nonprofits, communities & events", _rx(
        r"non[- ]?profit", r"nonprofit", r"communit(?:y|ies) (?:of|for)", r"meetups?", r"events?", r"newsletter",
        r"accelerator", r"incubator", r"venture (?:capital|fund|studio)", r"vc", r"investors?", r"grants?"),
        "Not an employer of engineers, usually."),
]

_BY_ID: dict[str, Segment] = {s.id: s for s in SEGMENTS}
UNCLASSIFIED = Segment("other:unclassified", "other", "Unclassified", re.compile(r"(?!x)x"),
                       "No description yet — nothing is inferred.")


def classify(*texts: str | None) -> tuple[Segment, list[Segment]]:
    """(primary, all matching). Primary is the first match in priority order."""
    blob = " ".join(t for t in texts if t).strip()
    if not blob:
        return UNCLASSIFIED, []
    hits = [s for s in SEGMENTS if s.pattern.search(blob)]
    if not hits:
        return UNCLASSIFIED, []
    return hits[0], hits


def segment(seg_id: str) -> Segment:
    return _BY_ID.get(seg_id, UNCLASSIFIED)


__all__ = ["LAYERS", "SEGMENTS", "UNCLASSIFIED", "Segment", "classify", "segment"]
