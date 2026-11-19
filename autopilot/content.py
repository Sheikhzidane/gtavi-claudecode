"""Category detection, hooks, editorial outro lines and per-platform titles/descriptions.

Free and offline: rules over the transcript. Hooks never claim facts the clip does not show.
"""
import hashlib, re

CATS = [  # (category, regex over transcript, hook options, outro options)
    ("police_chase", r"\b(cops?|police|wanted|stars?|chase|sirens?|swat|helicopter)\b",
     ["The cops did NOT expect this", "This chase got out of hand fast", "How did he escape this?"],
     ["Vice City police don't play around", "Would you have escaped that?"]),
    ("driving", r"\b(drive|driving|car|bike|drift|jump|crash(ed)?|speed|boat|plane|jet)\b",
     ["Watch the landing", "This drive should be impossible", "He really tried this jump"],
     ["GTA 6 driving physics are on another level", "Rate that driving 1-10"]),
    ("fail", r"\b(fail(ed)?|died|dead|wasted|oops|missed|fell|whoops|why did)\b",
     ["This went wrong so fast", "Wait for the fail", "He did not plan for this"],
     ["Wasted in record time", "We've all been there"]),
    ("secret", r"\b(secret|hidden|easter egg|found|discover(ed)?|rare|never seen|look at this)\b",
     ["Most players will miss this", "A hidden detail in GTA 6", "Did you know this was here?"],
     ["Save this for your playthrough", "Rockstar hid this on purpose"]),
    ("funny", r"\b(lmao|lol|haha+|funny|bro|dude|wtf|what the|nah)\b",
     ["I can't stop laughing at this", "Nobody expected this reaction", "Wait for his reaction"],
     ["GTA 6 NPCs are unhinged", "Tag someone who would do this"]),
    ("mission", r"\b(mission|heist|robbery|bank|objective|boss)\b",
     ["This mission is insane", "The heist starts here", "This mission had a twist"],
     ["No story spoilers past this point", "Which mission are you on?"]),
]
DEFAULT = ("epic", ["This GTA 6 moment is wild", "Watch till the end", "GTA 6 is unreal"],
           ["GTA 6 keeps surprising everyone", "Follow for a GTA 6 moment every day"])
TAGS = {"police_chase": ["#PoliceChase"], "driving": ["#GTA6Driving"], "fail": ["#GamingFails"], "secret": ["#GTA6Secrets", "#EasterEgg"],
        "funny": ["#Funny", "#GamingMoments"], "mission": ["#GTA6Missions"], "epic": ["#GamingClips"]}


def classify(text):
    t = text.lower(); best, hits = None, 0
    for cat, rx, hooks, outros in CATS:
        n = len(re.findall(rx, t))
        if n > hits:
            best, hits = (cat, hooks, outros), n
    return best or DEFAULT


def pick(options, seed):
    return options[int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(options)]


def write(transcript_text, creator_display, source_url, rights_row, brand, seed):
    cat, hooks, outros = classify(transcript_text)
    hook, outro = pick(hooks, seed), pick(outros, seed + "o")
    credit = f"Credit: {creator_display}" + (f" ({source_url})" if source_url else "")
    req_tags = (rights_row or {}).get("required_tags") or []
    mention = (rights_row or {}).get("required_mention") or ""
    ad = "#ad " if (rights_row or {}).get("ad_label") else ""
    tags = list(dict.fromkeys(req_tags + brand["tags"] + TAGS.get(cat, [])))
    title = f"{hook} | GTA 6"[:95]
    yt_title = (title if len(title) <= 92 else title[:92]) + " #shorts"
    body = f"{hook}. {outro}."
    return {
        "category": cat, "hook": hook, "outro": outro, "credit": credit,
        "youtube": {"title": yt_title[:100],
                    "description": f"{ad}{body}\n\n{credit}\n{mention}\n\nUnofficial fan clip. Not affiliated with Rockstar Games.\n{' '.join(tags)}".strip(),
                    "tags": [t.lstrip('#') for t in tags]},
        "tiktok": {"caption": f"{ad}{hook} {mention} {credit} {' '.join(tags[:6])}".replace("  ", " ")[:2150]},
        "instagram": {"caption": f"{ad}{body}\n\n{credit} {mention}\n.\n{' '.join(tags[:10])}"[:2200]},
    }
