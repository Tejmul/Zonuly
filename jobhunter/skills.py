"""The skill dictionary — one table both sides of a resume↔JD match read.

canonical name -> regexes (case-insensitive unless a pattern says (?-i:...)). Matching is on
word boundaries; the JD side and the resume side use the same table, so "Temporal" in a
posting and "migrated Trigger.dev to Temporal" in a bullet meet on one key. Pure data and
one function — no imports from the package, so the review gate (outreach/gates.py) and the
needs analysis (needs.py) can both stand on it.
"""

from __future__ import annotations

import re
from functools import lru_cache


SKILLS: dict[str, tuple[str, ...]] = {
    "Python": (r"\bpython\b",),
    "TypeScript": (r"\btypescript\b",),
    "JavaScript": (r"\bjavascript\b", r"\bjs\b"),
    "SQL": (r"\bsql\b",),
    "Go": (r"\bgolang\b", r"(?-i:\bGo\b)(?=\s*[,/)]|\s+(?:and|or|services|microservices|backend)\b)"),
    "Rust": (r"\brust\b",),
    "Java": (r"\bjava\b(?!\s*script)",),
    "C++": (r"c\+\+",),
    "C#/.NET": (r"\bc#", r"\.net\b"),
    "Kotlin": (r"\bkotlin\b",),
    "Swift/iOS": (r"\bswift\b", r"\bios\b"),
    "Ruby/Rails": (r"\bruby\b", r"\brails\b"),
    "PHP": (r"\bphp\b", r"\blaravel\b"),
    "Scala": (r"\bscala\b",),
    "Elixir": (r"\belixir\b",),
    "React": (r"\breact(?:\.js|js)?\b(?!\s+native)",),
    "React Native": (r"\breact native\b",),
    "Flutter": (r"\bflutter\b",),
    "Android": (r"\bandroid\b",),
    "Next.js": (r"\bnext\.?js\b",),
    "Node.js": (r"\bnode(?:\.js|js)?\b",),
    "Vue": (r"\bvue(?:\.js)?\b",),
    "Angular": (r"\bangular\b",),
    "FastAPI": (r"\bfastapi\b",),
    "Django": (r"\bdjango\b",),
    "Flask": (r"\bflask\b",),
    "Express": (r"\bexpress(?:\.js| js)\b",),
    "AWS": (r"\baws\b", r"amazon web services"),
    "Serverless": (r"\bserverless\b", r"\blambda\b"),
    "GCP": (r"\bgcp\b", r"google cloud"),
    "Azure": (r"\bazure\b",),
    "Docker": (r"\bdocker\b", r"\bcontainers?\b"),
    "Kubernetes": (r"\bkubernetes\b", r"\bk8s\b", r"\bhelm\b"),
    "Terraform": (r"\bterraform\b", r"infrastructure as code", r"\biac\b"),
    "CI/CD": (r"ci/cd", r"\bci\b", r"github actions", r"continuous (?:integration|delivery|deployment)"),
    "PostgreSQL": (r"\bpostgres(?:ql)?\b",),
    "MySQL": (r"\bmysql\b",),
    "MongoDB": (r"\bmongo(?:db)?\b",),
    "Redis": (r"\bredis\b",),
    "DynamoDB": (r"\bdynamodb\b",),
    "Kafka": (r"\bkafka\b",),
    "Elasticsearch": (r"\belasticsearch\b", r"\bopensearch\b"),
    "Spark": (r"\bspark\b", r"\bpyspark\b"),
    "Airflow": (r"\bairflow\b",),
    "Snowflake/dbt": (r"\bsnowflake\b", r"\bdbt\b", r"\bbigquery\b"),
    "LLMs": (r"\bllms?\b", r"large language models?", r"\bgpt-?\d", r"\bopenai\b", r"\banthropic\b", r"\bclaude\b",
             r"\bopenrouter\b", r"generative ai", r"\bgenai\b", r"\bfoundation models?\b"),
    "RAG": (r"\brag\b", r"retrieval[- ]augmented", r"\bretrieval\b"),
    "AI agents": (r"\bagentic\b", r"\bai agents?\b", r"\bmulti-agent\b", r"\bagents?\b(?= (?:that|which|to|for|framework|workflows?))",
                  r"\bllm agents?\b", r"\bmcp\b", r"model context protocol", r"\btool[- ]calling\b"),
    "LangChain": (r"\blangchain\b",),
    "LangGraph": (r"\blanggraph\b",),
    "Fine-tuning": (r"fine-?tun", r"\blora\b", r"\bqlora\b", r"\brlhf\b", r"\bsft\b", r"\bpeft\b"),
    "Prompt engineering": (r"prompt(?:ing)? (?:engineering|design|techniques)", r"\bprompts?\b"),
    "LLM evals": (r"\bevals?\b", r"evaluation (?:frameworks?|harness|pipelines?)", r"llm evaluation"),
    "Vector search": (r"vector (?:db|database|store|search|indexing)", r"\bpinecone\b", r"\bqdrant\b", r"\bweaviate\b",
                      r"\bfaiss\b", r"\bpgvector\b", r"\bchroma(?:db)?\b", r"\bmilvus\b", r"\bembeddings?\b"),
    "PyTorch": (r"\bpytorch\b", r"\btorch\b"),
    "TensorFlow": (r"\btensorflow\b", r"\bkeras\b"),
    "Hugging Face": (r"hugging ?face", r"\btransformers\b"),
    "Computer vision": (r"computer vision", r"\bopencv\b", r"\byolo", r"object detection", r"segmentation",
                        r"image (?:recognition|classification|processing)"),
    "NLP": (r"\bnlp\b", r"natural language processing"),
    "MLOps": (r"\bmlops\b", r"model (?:serving|deployment|inference)", r"\binference\b", r"\brunpod\b", r"\bvllm\b"),
    "Machine learning": (r"machine learning", r"\bml\b", r"random forest", r"scikit", r"\bxgboost\b"),
    "Deep learning": (r"deep learning", r"neural networks?"),
    "Web scraping": (r"\bscrap(?:e|er|ers|ing)\b", r"\bcrawl(?:er|ers|ing)?\b", r"\bapify\b"),
    "Data pipelines": (r"data pipelines?", r"\betl\b", r"\belt\b", r"data engineering", r"data ingestion", r"\bpandas\b"),
    "Workflow orchestration": (r"\btemporal\b", r"trigger\.dev", r"workflow orchestration", r"\bcelery\b",
                               r"job queues?", r"background jobs", r"\bcron\b"),
    "Streaming": (r"server-sent events", r"\bsse\b", r"\bwebsockets?\b", r"\bstreaming\b"),
    "Distributed systems": (r"distributed systems?",),
    "Microservices": (r"\bmicroservices?\b",),
    "APIs": (r"\brest(?:ful)? apis?\b", r"\bapi design\b", r"\bapis\b", r"\bwebhooks?\b"),
    "GraphQL": (r"\bgraphql\b",),
    "Frontend": (r"\bfront-?end\b", r"\bui engineering\b", r"\btailwind\b", r"\bcss\b"),
    "Backend": (r"\bback-?end\b", r"server-side"),
    "Full-stack": (r"full[- ]?stack",),
    "DevOps": (r"\bdevops\b", r"\bsre\b", r"site reliability", r"\bnginx\b", r"\bdns\b"),
    "Observability": (r"\bobservability\b", r"\bopentelemetry\b", r"\bprometheus\b", r"\bgrafana\b", r"\bdatadog\b",
                      r"\btracing\b", r"\bmonitoring\b"),
    "Cloud migration": (r"\bmigrat(?:e|ed|ion|ing)\b",),
    "Security": (r"\bappsec\b", r"application security", r"penetration testing", r"threat model", r"\bcognito\b",
                 r"\bauth(?:entication|orization)?\b"),
    "Blockchain": (r"\bsolidity\b", r"\bblockchain\b", r"\bweb3\b", r"smart contracts?"),
    "Embedded": (r"\bembedded\b", r"\bfirmware\b", r"\brtos\b"),
    "Linux": (r"\blinux\b", r"\bbash\b"),
}


# Generic asks answered by concrete work: "backend experience" is covered by a shipped
# FastAPI / Lambda / microservices bullet even when the bullet never says "backend".
IMPLIED_BY: dict[str, tuple[str, ...]] = {
    "Backend": ("FastAPI", "Node.js", "Express", "Serverless", "Microservices", "PostgreSQL", "Django", "Flask"),
    "APIs": ("FastAPI", "Express", "Node.js", "Serverless", "Microservices", "Streaming"),
    "Frontend": ("React", "Next.js", "Vue", "Angular"),
    "Full-stack": ("Next.js",),
    "Machine learning": ("Deep learning", "Computer vision", "Fine-tuning", "PyTorch", "TensorFlow"),
    "Deep learning": ("Fine-tuning", "Computer vision"),
    "Cloud migration": (),
    "DevOps": ("CI/CD", "Docker"),
}


@lru_cache(maxsize=1)
def _compiled() -> dict[str, re.Pattern]:
    return {k: re.compile("|".join(v), re.I) for k, v in SKILLS.items()}


def skills_in(text: str | None) -> list[str]:
    """Canonical skills a text names, in dictionary order."""
    if not text:
        return []
    return [k for k, rx in _compiled().items() if rx.search(text)]


def pattern(skill: str) -> str:
    """One regex for a canonical skill (or the literal text for an unknown one)."""
    return "|".join(SKILLS.get(skill) or (re.escape(skill),))


__all__ = ["SKILLS", "IMPLIED_BY", "skills_in", "pattern"]
